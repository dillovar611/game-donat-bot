"""
Хотираи доимии FSM дар MySQL — то ҳангоми РЕСТАРТ ҳолати мизоҷон гум нашавад.

Пеш MemoryStorage (муваққатӣ) буд: ҳар рестарт ҳамаи флоуҳои нимкораро пок
мекард, ва чекҳое, ки дар ҳамон лаҳза меомаданд, «мӯҳлат гузашт» мегирифтанд.
Ин storage ҳолат+додаро дар ҷадвали fsm_states нигоҳ медорад.
"""
import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.state import State

import database as db

logger = logging.getLogger(__name__)


def _key(key: StorageKey) -> str:
    return ":".join([
        str(key.bot_id),
        str(key.chat_id),
        str(key.user_id),
        str(getattr(key, "thread_id", None) or 0),
        str(getattr(key, "business_connection_id", None) or ""),
        str(getattr(key, "destiny", None) or "default"),
    ])


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


class MySQLStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state=None) -> None:
        s = state.state if isinstance(state, State) else state
        try:
            await db.fsm_set_state(_key(key), s)
        except Exception as e:
            logger.error(f"fsm set_state хато: {e}")

    async def get_state(self, key: StorageKey) -> Optional[str]:
        try:
            return await db.fsm_get_state(_key(key))
        except Exception as e:
            logger.error(f"fsm get_state хато: {e}")
            return None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        try:
            raw = json.dumps(data or {}, ensure_ascii=False, default=_default)
            await db.fsm_set_data(_key(key), raw)
        except Exception as e:
            logger.error(f"fsm set_data хато: {e}")

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        try:
            raw = await db.fsm_get_data(_key(key))
            return json.loads(raw) if raw else {}
        except Exception as e:
            logger.error(f"fsm get_data хато: {e}")
            return {}

    async def close(self) -> None:
        pass
