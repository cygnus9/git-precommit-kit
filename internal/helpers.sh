#!/bin/bash
# GPK helper routines

## apply_check CHECK TARGET
function apply_check {
    local check="$1"
    local new_file="$new_version"/"$2"
    local old_file="$old_version"/"$2"
    (cd "$new_version"/gpk/checks && export PATH=$PATH:. && $check "$new_file" "$old_file" > "$one_log" 2>&1) || {
        if [[ $? -eq 127 ]]; then not_found=true; fi
        printf "[${red}fail${normal}] %-20s %s\n" "$1" "$2" >&2
        cat "$one_log" >&2
        exit_code=false
    }
}

## apply_rules <RULES_FILE> <TARGET> <SHORTNAME>
# Apply all rules in RULES_FILE to TARGET
function apply_rules {
    while read -r rule; do
        IFS=':' read -ra parts <<< "$rule"

        for glob in ${parts[0]}; do
            if $(set -f; [[ "$2" == $glob ]]); then
                # Execute the consequent part of the rule (stripping initial
                # space off the consequent if there)
                apply_check "${parts[1]# }" "$2"
                break  # Only one glob per rule per customer
            fi
        done
    done < "$1"
}

## read_rules <HANDLER> <RULES_FILE>
# For every rule, call the handler with the glob and the rule in separate arguments
function read_rules {
    while read -r rule; do
        IFS=':' read -ra parts <<< "$rule"

        $1 "${parts[0]}" "${parts[1]}"
    done < "$2"
}

## apply_precommits FILE
# Find the precommit rule for a file and execute it
function apply_precommits {
    local inrepo_dir="$(dirname "$1")"
    local dir="$new_version"/"$inrepo_dir"

    if [[ -f "$dir"/PRECOMMIT ]]; then apply_rules "$dir"/PRECOMMIT "$1"; fi
    while [[ "$inrepo_dir" != "." ]]; do
        inrepo_dir="$(dirname "$inrepo_dir")"
        dir="$new_version"/"$inrepo_dir"
        if [[ -f "$dir"/PRECOMMIT ]]; then apply_rules "$dir"/PRECOMMIT "$1"; fi
    done
}
