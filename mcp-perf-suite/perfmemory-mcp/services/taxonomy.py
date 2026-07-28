import logging
from typing import Optional, Tuple, List, Dict, Any

from utils.config import load_taxonomy

log = logging.getLogger(__name__)


class TaxonomyResolver:
    """Resolves aliases and validates values against the taxonomy YAML.

    The taxonomy is loaded once and cached. All lookups are case-insensitive.

    Terminology mapping:
        - "application" (taxonomy YAML) = "system_under_test" (relational DB)
          = "Project" node (Apache AGE graph). These are a one-to-one mapping.
    """

    def __init__(self, taxonomy_path: str = ""):
        self._raw = load_taxonomy(taxonomy_path)
        self._lookup: Dict[str, Dict[str, str]] = {}
        self._app_lookup: Dict[str, Dict[str, Any]] = {}
        self._env_lookup: Dict[str, Dict[str, Any]] = {}
        self._build_lookups()

    def _build_lookups(self):
        """Build case-insensitive alias-to-canonical lookup tables."""
        for category in ("environment_types", "auth_flow_types",
                         "error_categories", "protocol_types"):
            entries = self._raw.get(category, [])
            mapping: Dict[str, str] = {}
            for entry in entries:
                canonical = entry.get("name", "")
                if not canonical:
                    continue
                mapping[canonical.lower()] = canonical
                for alias in entry.get("aliases", []):
                    mapping[alias.lower()] = canonical
            self._lookup[category] = mapping

        for app in self._raw.get("applications", []):
            name = app.get("name", "")
            alias = app.get("alias", "")
            if name:
                self._app_lookup[name.lower()] = app
            if alias:
                self._app_lookup[alias.lower()] = app
            for alt in app.get("aliases", []):
                if alt:
                    self._app_lookup[alt.lower()] = app

        for env in self._raw.get("environments", []):
            env_name = env.get("name", "")
            if env_name:
                self._env_lookup[env_name.lower()] = env

    @property
    def is_loaded(self) -> bool:
        return bool(self._raw)

    def resolve_alias(self, category: str, value: str) -> str:
        """Resolve a value to its canonical name if it's a known alias.

        Args:
            category: One of environment_types, auth_flow_types,
                      error_categories, protocol_types.
            value: The value to resolve.

        Returns:
            Canonical name if an alias match is found, otherwise the
            original value unchanged.
        """
        if not value:
            return value
        mapping = self._lookup.get(category, {})
        return mapping.get(value.lower(), value)

    def resolve_application(self, system_under_test: str) -> Optional[Dict[str, Any]]:
        """Look up an application by name, alias, or any entry in aliases.

        Accepts the canonical application name (e.g., "Online Shopping Portal"),
        the short alias (e.g., "OSP"), or any alternative name registered in the
        application's ``aliases`` list. The resolved name maps to
        system_under_test in the relational DB and Project.name in the
        knowledge graph.

        Returns:
            The application dict from taxonomy if found, None otherwise.
        """
        if not system_under_test:
            return None
        return self._app_lookup.get(system_under_test.lower())

    def resolve_environment(self, environment: str) -> Optional[Dict[str, Any]]:
        """Look up a specific environment instance by name.

        Accepts a specific environment name (e.g., "QA1", "STG-East", "PERF-01")
        and returns the full environment dict from taxonomy.yaml[environments]
        if found. The returned dict includes the ``type`` field which maps to
        the ``env_type`` column in the database.

        Args:
            environment: The specific environment name to look up.

        Returns:
            The environment dict from taxonomy if found, None otherwise.
        """
        if not environment:
            return None
        return self._env_lookup.get(environment.lower())

    def get_env_type(self, environment: str) -> str:
        """Derive the canonical environment type from a specific environment name.

        Looks up the environment in taxonomy.yaml[environments] and returns
        its ``type`` field (e.g., "QA1" → "qa", "STG-East" → "staging").

        If the environment name is not found in taxonomy, returns empty string.

        Args:
            environment: The specific environment name.

        Returns:
            The canonical environment type, or "" if not found.
        """
        if not environment:
            return ""
        env = self._env_lookup.get(environment.lower())
        if env:
            return env.get("type", "")
        return ""

    def validate(self, category: str, value: str) -> Tuple[bool, str, str]:
        """Validate a value against a taxonomy category.

        Args:
            category: One of environment_types, auth_flow_types,
                      error_categories, protocol_types.
            value: The value to validate.

        Returns:
            Tuple of (is_valid, canonical_name, warning_message).
            is_valid is True if the value (or an alias) exists in the taxonomy.
            canonical_name is the resolved name.
            warning_message is empty if valid, descriptive if not.
        """
        if not value:
            return (True, value, "")

        mapping = self._lookup.get(category, {})
        if not mapping:
            return (True, value, "")

        canonical = mapping.get(value.lower())
        if canonical:
            return (True, canonical, "")

        return (
            False,
            value,
            f"'{value}' is not defined in taxonomy category '{category}'. "
            f"Known values: {', '.join(sorted(set(mapping.values())))}",
        )

    def validate_session_fields(
        self,
        system_under_test: str = "",
        environment: str = "",
        env_type: str = "",
        auth_flow_type: str = "",
        system_alias: str = "",
    ) -> List[str]:
        """Validate all session-level fields against the taxonomy.

        Args:
            system_under_test: Application name to validate against applications.
            environment: Specific environment name (e.g., "QA1", "STG-East") to
                validate against taxonomy.yaml[environments].
            env_type: Canonical environment type (e.g., "qa", "staging") to
                validate against taxonomy.yaml[environment_types].
            auth_flow_type: Auth flow type to validate against auth_flow_types.
            system_alias: App alias used as fallback for application lookup.

        Returns a list of warning messages (empty if all valid).
        """
        warnings: List[str] = []

        if system_under_test and self._app_lookup:
            app = self.resolve_application(system_under_test)
            if not app and system_alias:
                app = self.resolve_application(system_alias)
            if not app:
                known = [a.get("name", "") for a in self._raw.get("applications", [])]
                aliases = [a.get("alias", "") for a in self._raw.get("applications", []) if a.get("alias")]
                known_str = ", ".join(f"{n} ({a})" for n, a in zip(known, aliases) if n)
                if known_str:
                    warnings.append(
                        f"Application '{system_under_test}' is not defined in taxonomy. "
                        f"Known applications: {known_str}"
                    )

        if environment and self._env_lookup:
            env = self.resolve_environment(environment)
            if not env:
                known_envs = ", ".join(sorted(self._env_lookup.keys()))
                if known_envs:
                    warnings.append(
                        f"Environment '{environment}' is not defined in taxonomy. "
                        f"Known environments: {known_envs}"
                    )

        if env_type:
            valid, _, msg = self.validate("environment_types", env_type)
            if not valid:
                warnings.append(msg)

        if auth_flow_type:
            valid, _, msg = self.validate("auth_flow_types", auth_flow_type)
            if not valid:
                warnings.append(msg)

        return warnings

    def validate_attempt_fields(
        self,
        error_category: str = "",
    ) -> List[str]:
        """Validate attempt-level fields against the taxonomy.

        Returns a list of warning messages (empty if all valid).
        """
        warnings: List[str] = []

        if error_category:
            valid, _, msg = self.validate("error_categories", error_category)
            if not valid:
                warnings.append(msg)

        return warnings
