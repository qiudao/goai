import requests

from leptonai.util import create_header, check_and_print_http_error


def list_deployment(url: str, auth_token: str):
    """
    List all deployments on a workspace.
    """
    response = requests.get(url + "/deployments", headers=create_header(auth_token))
    if check_and_print_http_error(response):
        return None
    return response.json()


def remove_deployment(url: str, auth_token: str, name: str):
    """
    Remove a deployment from a workspace.
    """
    response = requests.delete(
        url + "/deployments/" + name, headers=create_header(auth_token)
    )
    if check_and_print_http_error(response):
        return None
    return True
