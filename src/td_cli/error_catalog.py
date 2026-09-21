"""Closed vocabulary of public error codes shared by the CLI, Daemon and Agent Component."""

from __future__ import annotations

from collections.abc import Iterable


class ErrorCatalog:
    """Public error codes and the retry safety each one proves.

    ``retryable`` is true only when the code proves the Command never started and the
    condition is transient, so submitting the same Command as a new Request cannot duplicate
    a side effect. Every other code is false, including timeouts and transport failures after
    which the Request may exist: inspect it by Request ID instead of submitting again.
    """

    def __init__(self, codes: Iterable[str], *, retryable: Iterable[str]) -> None:
        ordered = tuple(codes)
        self._codes = frozenset(ordered)
        self._retryable = frozenset(retryable)
        if len(self._codes) != len(ordered):
            raise ValueError("error codes must be unique")
        if not self._retryable <= self._codes:
            raise ValueError("retryable error codes must be catalogued")

    @property
    def codes(self) -> frozenset[str]:
        return self._codes

    def __contains__(self, code: object) -> bool:
        return code in self._codes

    def retryable(self, code: str) -> bool:
        return code in self._retryable

    def error(self, code: str) -> dict[str, object]:
        """Build a terminal Request error; uncatalogued codes are kept but never retryable."""
        return {"code": code, "message": code, "details": {}, "retryable": self.retryable(code)}


ERROR_CATALOG = ErrorCatalog(
    (
        # Client and Daemon transport
        "daemon_unavailable",
        "transport_error",
        "invalid_arguments",
        "protocol_incompatible",
        "wait_timeout",
        "process_launch_failed",
        "internal_error",
        # Instance selection and Request admission
        "instance_not_found",
        "instance_selector_ambiguous",
        "instance_offline",
        "instance_draining",
        "instance_synchronizing",
        "instance_busy",
        "daemon_shutdown",
        "command_unsupported",
        # Request identity and execution
        "request_not_found",
        "request_id_conflict",
        "request_identity_invalid",
        "request_rejected",
        "command_wire_invalid",
        "execution_capacity_full",
        "request_outcome_unknown",
        "outcome_capacity_exceeded",
        "result_too_large",
        # Operators
        "operator_not_found",
        "operator_parent_invalid",
        "operator_already_exists",
        "operator_create_failed",
        "operator_type_unsupported",
        "operator_type_conditional",
        "operator_mutation_forbidden",
        "operator_rename_forbidden",
        "operator_rename_failed",
        "operator_rename_rollback_failed",
        "operator_not_empty",
        "operator_connected",
        "operator_destroy_failed",
        "operator_destroy_outcome_unknown",
        "operator_docked",
        "operator_copy_failed",
        "operator_copy_rollback_failed",
        "operator_move_failed",
        "operator_move_rollback_failed",
        "operator_move_outcome_unknown",
        "operator_state_unavailable",
        "operator_state_failed",
        "operator_state_rollback_failed",
        "operator_state_outcome_unknown",
        "operator_family_unsupported",
        "operator_family_mismatch",
        "family_inspection_unavailable",
        "family_inspection_outcome_unknown",
        # Trusted TOX Import
        "tox_trust_required",
        "tox_path_rejected",
        "tox_file_too_large",
        "tox_destination_exists",
        "tox_parent_protected",
        "tox_load_failed",
        "tox_verification_failed",
        "tox_backup_failed",
        "tox_commit_failed",
        "tox_rollback_failed",
        "tox_import_outcome_unknown",
        # DAT content
        "dat_type_mismatch",
        "dat_content_unavailable",
        "dat_content_not_writable",
        "dat_content_too_large",
        "text_dat_write_failed",
        "text_dat_rollback_failed",
        "text_dat_outcome_unknown",
        "table_dat_patch_out_of_bounds",
        "table_dat_write_failed",
        "table_dat_rollback_failed",
        "table_dat_outcome_unknown",
        # Regular Connections and COMP Hierarchy Connections
        "connector_not_found",
        "connector_occupied",
        "connector_connect_failed",
        "connector_disconnect_failed",
        "connector_replace_failed",
        "connector_replace_rollback_failed",
        "connection_not_found",
        "hierarchy_comp_required",
        "hierarchy_kind_unsupported",
        "hierarchy_kind_mismatch",
        "hierarchy_connector_not_found",
        "hierarchy_connector_state_ambiguous",
        "hierarchy_connector_occupied",
        "hierarchy_connection_not_found",
        "hierarchy_connector_connect_failed",
        "hierarchy_connector_disconnect_failed",
        "hierarchy_connector_replace_failed",
        "hierarchy_connector_replace_rollback_failed",
        "hierarchy_connector_outcome_unknown",
        "hierarchy_cycle",
        "hierarchy_parent_mismatch",
        # Parameters
        "parameter_not_found",
        "parameter_exists",
        "parameter_read_only",
        "parameter_disabled",
        "parameter_obsolete",
        "parameter_not_pulseable",
        "parameter_type_unsupported",
        "parameter_value_invalid",
        "parameter_value_too_large",
        "parameter_write_rejected",
        "expression_invalid",
        "parameter_source_not_found",
        "parameter_export_source_unavailable",
        "parameter_rollback_failed",
        "parameter_outcome_unknown",
        "parameter_page_exists",
        "parameter_page_failed",
        "parameter_page_verification_failed",
        "parameter_page_rollback_failed",
        "parameter_page_outcome_unknown",
        "parameter_sequence_not_found",
        "parameter_sequence_too_large",
        "parameter_sequence_not_writable",
        "parameter_sequence_shape_invalid",
        "parameter_sequence_write_failed",
        "parameter_sequence_rollback_failed",
        "parameter_sequence_outcome_unknown",
        # Project save
        "project_path_mismatch",
        "project_file_changed",
        "project_file_unavailable",
        "project_save_outcome_unknown",
    ),
    retryable=(
        "instance_busy",
        "instance_offline",
        "instance_draining",
        "instance_synchronizing",
        "daemon_shutdown",
        "execution_capacity_full",
    ),
)
