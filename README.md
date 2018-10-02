# steelconnect_easy_ssh
Easy way to SSH to SteelConnect devices, via either SCM SSH Tunnel or directly via SSH

## Getting Started
USAGE:
    steelconnect_easy_ssh.py, no parameters required.
    Rename config.ini.example to config.ini to set SCM credentials, optional.

### Prerequisites
- Python 3.6 or higher.
- OS with OpenSSH client (Linux, Mac, Windows 10 w/ April 2018 update**)
- REST API + SSH enabled in SCM realm and public key of client in SCM
- requests module
- steelconnection module

** = Windows 10 w/April 2018 supports OpenSSH, but no nc/HTTP proxy support so only direct SSH will work, no tunnel support.

To install the Requests & SteelConnection modules:
- pip3 install requests steelconnection, or
- pip install requests steelconnection
