// Cedar policies for the lending MCP gateway.
// The same files load into AgentCore Policy later (entity names adapt to the
// AgentCore::* namespace). Default is deny: with no permit, nothing runs.
// Forbid always wins over permit.
// Every policy needs an @id: the gateway uses it as the denial reason code.

@id("base-permit-registered-agents")
permit (principal, action, resource)
when { principal.agent_registered };

@id("field-officer-own-branch-only")
forbid (principal, action in Action::"case-read", resource)
when { principal.role == "field_officer" && context.loan_branch != principal.branch };

@id("write-tools-need-write-role")
forbid (principal, action in Action::"write", resource)
unless {
  principal.role == "credit_manager" ||
  (principal.role == "field_officer" && action == Action::"collections.log_promise_to_pay")
};

@id("money-moving-needs-human-approval")
forbid (principal, action in Action::"money-moving", resource)
unless { principal.role == "credit_manager" && context.approval_token_valid };

@id("code-mode-is-read-only")
forbid (principal, action in Action::"write", resource)
when { context.via_code };

@id("code-token-bound-to-one-case")
forbid (principal, action, resource)
when { context.via_code && !context.case_in_scope };
