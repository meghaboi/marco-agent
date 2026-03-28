from __future__ import annotations

import os
from dataclasses import dataclass


class SecretProvider:
    def get_secret(self, *, key: str) -> str | None:
        raise NotImplementedError

    def set_secret(self, *, key: str, value: str) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class EnvSecretProvider(SecretProvider):
    def get_secret(self, *, key: str) -> str | None:
        value = os.environ.get(key)
        return value.strip() if value else None

    def set_secret(self, *, key: str, value: str) -> None:
        os.environ[key] = value


class KeyVaultSecretProvider(SecretProvider):
    def __init__(self, *, vault_url: str | None) -> None:
        self._vault_url = (vault_url or "").strip()
        self._fallback = EnvSecretProvider()

    @property
    def enabled(self) -> bool:
        return bool(self._vault_url)

    def get_secret(self, *, key: str) -> str | None:
        if not self.enabled:
            return self._fallback.get_secret(key=key)
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except Exception:
            return self._fallback.get_secret(key=key)
        client = SecretClient(vault_url=self._vault_url, credential=DefaultAzureCredential())
        try:
            secret = client.get_secret(key)
        except Exception:
            return None
        value = str(getattr(secret, "value", "")).strip()
        return value or None

    def set_secret(self, *, key: str, value: str) -> None:
        if not self.enabled:
            self._fallback.set_secret(key=key, value=value)
            return
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except Exception:
            self._fallback.set_secret(key=key, value=value)
            return
        client = SecretClient(vault_url=self._vault_url, credential=DefaultAzureCredential())
        client.set_secret(key, value)
