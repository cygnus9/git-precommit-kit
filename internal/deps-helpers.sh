#!/bin/bash
# Helper routines for dependency scripts

## avail <TOOL>
# Whether a given tool is installed
function avail {
    which "$1" >/dev/null
}
export -f avail

## try-apt <PACKAGE>
# Succeeds if apt is not available, fails if the installation failed
function try-apt {
    if avail apt-get; then
        echo Installing $1 from apt. You may need to enter your password.
        sudo apt-get install $1
    fi
}

## try-pip <PACKAGE>
function try-pip {
    if ! avail pip; then
        if avail easy_install; then
            sudo easy_install pip || exit 1
        fi
    fi

    if ! avail pip; then exit 0; fi

    echo Installing $1 from pip. You may need to enter your password.
    sudo pip install $1
}
