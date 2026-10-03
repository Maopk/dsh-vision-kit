@{
    # Error + Warning only.  Information-level hits are style advice (e.g. positional
    # parameters) and would turn every routine edit into a lint argument.
    Severity = @('Error', 'Warning')

    ExcludeRules = @(
        # These are console tools for one operator: Write-Host is the intended output, and
        # its alternative (Write-Output) would pollute the pipeline of callers.
        'PSAvoidUsingWriteHost',

        # False positive on actor.ps1: -HomePath IS used, by Resolve-Home; the rule only
        # looks at the script scope, so it cannot see it.
        'PSReviewUnusedParameter'
    )
}
