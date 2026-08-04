# MCP versioning

MCP tools expose RailWarden mechanics, not arbitrary supervisor decisions. Tool names and required input/output fields are provisional until documented stable. Additive optional fields are preferred; removing or changing a required field is breaking and requires a major pre-1.0 compatibility decision, migration notes, and contract tests. MCP clients should tolerate unknown optional fields and check the runtime/package version.
