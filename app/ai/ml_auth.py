import os
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
from app.database import get_connection

logger = logging.getLogger(__name__)

ML_AUTH_URL = 'https://auth.mercadolivre.com.br/authorization'
ML_TOKEN_URL = 'https://api.mercadolibre.com/oauth/token'
ML_PROVIDER = 'mercadolivre'
TOKEN_EXPIRY_BUFFER = 300


class MLAuthError(Exception):
    pass


class MLNotAuthorizedError(MLAuthError):
    pass


class MLRefreshFailedError(MLAuthError):
    pass


class MLAuth:

    def __init__(self):
        self.app_id = os.environ['ML_APP_ID']
        self.client_secret = os.environ['ML_CLIENT_SECRET']
        self.redirect_uri = os.environ.get(
            'ML_REDIRECT_URI',
            'https://afiliados.postmills.com.br/oauth/callback'
        )

    def get_authorization_url(self):
        params = urlencode({
            'response_type': 'code',
            'client_id': self.app_id,
            'redirect_uri': self.redirect_uri,
        })
        return '%s?%s' % (ML_AUTH_URL, params)

    def exchange_code_for_token(self, code):
        try:
            resp = requests.post(ML_TOKEN_URL, data={
                'grant_type': 'authorization_code',
                'client_id': self.app_id,
                'client_secret': self.client_secret,
                'code': code,
                'redirect_uri': self.redirect_uri,
            }, timeout=15)
            resp.raise_for_status()
        except requests.HTTPError as e:
            logger.error('[ML-AUTH] exchange falhou status=%s' % resp.status_code)
            raise MLAuthError('exchange falhou: %s' % resp.status_code) from e
        except requests.RequestException as e:
            logger.error('[ML-AUTH] Erro de rede no exchange: %s' % e)
            raise MLAuthError('Erro de rede: %s' % e) from e
        token_data = resp.json()
        self.save_token(token_data)
        logger.info('[ML-AUTH] Autorizacao concluida user_id=%s' % token_data.get('user_id'))
        return token_data

    def refresh_access_token(self):
        row = self.load_token()
        if not row or not row.get('refresh_token'):
            raise MLNotAuthorizedError('Sem refresh_token salvo')
        try:
            resp = requests.post(ML_TOKEN_URL, data={
                'grant_type': 'refresh_token',
                'client_id': self.app_id,
                'client_secret': self.client_secret,
                'refresh_token': row['refresh_token'],
            }, timeout=15)
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = resp.status_code
            if status in (400, 401):
                self._mark_invalid()
                raise MLRefreshFailedError('Refresh rejeitado status=%s' % status) from e
            logger.error('[ML-AUTH] Refresh HTTP erro %s' % status)
            raise MLAuthError('Refresh falhou: %s' % status) from e
        except requests.RequestException as e:
            logger.error('[ML-AUTH] Erro de rede no refresh: %s' % e)
            raise MLAuthError('Erro de rede: %s' % e) from e
        new_data = resp.json()
        self.save_token(new_data)
        logger.info('[ML-AUTH] Token renovado expira em %ss' % new_data.get('expires_in'))
        return new_data

    def get_valid_token(self):
        row = self.load_token()
        if row is None:
            raise MLNotAuthorizedError('Usuario nao autorizou - acesse /oauth/authorize')
        if row.get('status') == 'invalid':
            raise MLNotAuthorizedError('Token invalido - acesse /oauth/authorize')
        expires_at = row.get('expires_at')
        needs_refresh = True
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(str(expires_at))
                seconds_left = (exp_dt - datetime.utcnow()).total_seconds()
                needs_refresh = seconds_left < TOKEN_EXPIRY_BUFFER
            except (ValueError, TypeError):
                needs_refresh = True
        if needs_refresh:
            self.refresh_access_token()
            row = self.load_token()
        return row['access_token']

    def save_token(self, token_data):
        expires_in = token_data.get('expires_in', 21600)
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        updated_at = datetime.utcnow().isoformat()
        conn = get_connection()
        try:
            conn.execute(
                'INSERT OR REPLACE INTO oauth_tokens'
                ' (provider, access_token, refresh_token, expires_at, scope, user_id, status, updated_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    ML_PROVIDER,
                    token_data['access_token'],
                    token_data.get('refresh_token'),
                    expires_at,
                    token_data.get('scope', ''),
                    str(token_data.get('user_id', '')),
                    'active',
                    updated_at,
                )
            )
            conn.commit()
        except Exception as e:
            logger.error('[ML-AUTH] Erro ao salvar token: %s' % e)
            raise
        finally:
            conn.close()

    def load_token(self):
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute('SELECT * FROM oauth_tokens WHERE provider = ?', (ML_PROVIDER,))
            row = c.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def is_connected(self):
        row = self.load_token()
        if row is None:
            return False
        if row.get('status') == 'invalid':
            return False
        return True

    def _mark_invalid(self):
        updated_at = datetime.utcnow().isoformat()
        conn = get_connection()
        try:
            conn.execute(
                'UPDATE oauth_tokens SET status = ?, updated_at = ? WHERE provider = ?',
                ('invalid', updated_at, ML_PROVIDER)
            )
            conn.commit()
        except Exception as e:
            logger.error('[ML-AUTH] Erro ao marcar token invalido: %s' % e)
        finally:
            conn.close()
        logger.error('[ML-AUTH] Token marcado invalido - reautorizacao necessaria')
