"""CareFlow model provider layer: one async interface (chat, streaming, validated structured
output) over local, API and subscription-CLI providers, chosen per task in config/providers.yaml.
"""

from careflow_providers.adapters.replay import ReplayProvider
from careflow_providers.base import (
    ChatProvider,
    ChatResult,
    Message,
    StructuredFailure,
    StructuredOk,
    structured,
)
from careflow_providers.config import (
    ApiTaskConfig,
    CliTaskConfig,
    ConfigError,
    LocalTaskConfig,
    ProvidersConfig,
    TaskName,
    load_config,
    parse_config,
)
from careflow_providers.errors import CassetteMiss, LivePathForbidden, ProviderUnavailable
from careflow_providers.factory import build_provider

__version__ = "0.0.0"

__all__ = [
    "ApiTaskConfig",
    "CassetteMiss",
    "ChatProvider",
    "ChatResult",
    "CliTaskConfig",
    "ConfigError",
    "LivePathForbidden",
    "LocalTaskConfig",
    "Message",
    "ProviderUnavailable",
    "ProvidersConfig",
    "ReplayProvider",
    "StructuredFailure",
    "StructuredOk",
    "TaskName",
    "build_provider",
    "load_config",
    "parse_config",
    "structured",
]
