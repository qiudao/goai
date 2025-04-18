import base64
import os
import re
import requests
from typing import Dict, Tuple, Union
from urllib.parse import urlparse

from leptonai.photon import FileParam


def _is_valid_url(candidate_str: str) -> bool:
    parsed = urlparse(candidate_str)
    return parsed.scheme != "" and parsed.netloc != ""


def _is_local_url(candidate_str: str) -> bool:
    parsed = urlparse(candidate_str)
    local_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
    return parsed.hostname in local_hosts


def _json_to_type_string(schema: Dict) -> str:
    """
    Internal util function to convert a json to a type string.
    """
    if "type" in schema:
        if "items" in schema:
            # items defines the type of items in an array.
            typestr = f"{schema['type']}[{_json_to_type_string(schema['items'])}]"
        elif "prefixItems" in schema:
            # repeateditems defines the type of the first few items in an array, and
            # then the min and max of the array.
            # In pythonic term, this is something like Tuple[int, str, ...]
            # meaning that the first thing is int, the second thing is str, and there are a bunch of others.
            min_items = schema.get("minItems", "?")
            max_items = schema.get("maxItems", "?")
            if min_items == max_items and min_items != "?":
                typestr = (
                    f"{schema['type']}[{', '.join(_json_to_type_string(x) for x in schema['prefixItems'])}]"
                )
            else:
                typestr = (
                    f"{schema['type']}[{', '.join(_json_to_type_string(x) for x in schema['prefixItems'])},"
                    f" ...] (min={min_items},max={max_items})"
                )
        else:
            typestr = {
                "integer": "int",
                "number": "float",
                "boolean": "bool",
                "string": "str",
                "null": "None",
            }.get(schema["type"], schema["type"])

    elif "anyOf" in schema:
        typestr = f"({' | '.join(_json_to_type_string(x) for x in schema['anyOf'])})"
    else:
        # If we have no idea waht the type is, we will just return "Any".
        typestr = "Any"
    if "default" in schema:
        typestr += f" (default: {schema['default']})"
    return typestr


def _get_api_info(openapi: Dict, path_name: str) -> Tuple[Dict, bool]:
    """
    Get the api information of the given path from the openapi specification.
    If the api info is not found, return an empty dict.
    """
    try:
        api_info = openapi["paths"][f"{path_name}"]["post"]
        is_post = True
    except KeyError:
        # No path or post info exists: this is probably a get method.
        try:
            api_info = openapi["paths"][f"{path_name}"]["get"]
            is_post = False
        except KeyError:
            # No path or get info exists: we will just return an empty docstring.
            api_info = {}
            is_post = False
    return (api_info, is_post)


def _get_post_input_schema(openapi: Dict, api_info: Dict) -> Dict:
    """
    Get the input schema from the api info.

    Raises KeyError if the schema is not found.
    """
    schema_ref = api_info["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    schema_name = schema_ref.split("/")[-1]
    schema = openapi["components"]["schemas"][schema_name]
    return schema


def _get_method_docstring(openapi: Dict, path_name: str) -> str:
    """
    Get the docstring for a method from the openapi specification.
    """
    api_info, is_post = _get_api_info(openapi, path_name)
    if not api_info:
        return ""

    # Add description to docstring. We will use the description, and if not,
    # the summary as a backup plan, and if not, we will not add a docstring.
    docstring = api_info.get("description", api_info.get("summary", ""))

    if not is_post:
        # TODO: add support to parse get methods' parameters.
        return docstring

    docstring += "\n\nAutomatically inferred parameters from openapi:"
    # Add schema to the docstring.
    try:
        schema = _get_post_input_schema(openapi, api_info)
        schema_strings = [
            (k, _json_to_type_string(v)) for k, v in schema["properties"].items()
        ]
        if len(schema_strings) == 0:
            docstring += "\n\nInput Schema: None"
        elif "required" in schema:
            # We will sort the schema strings to make required fields appear first
            schema_strings = sorted(
                schema_strings, key=lambda x: x[0] in schema["required"], reverse=True
            )
            docstring += "\n\nInput Schema (*=required):\n  " + "\n  ".join(
                [
                    f"{k}{'*' if k in schema['required'] else ''}: {v}"
                    for k, v in schema_strings
                ]
            )
        else:
            docstring += "\n\nSchema:\n  " + "\n  ".join(
                [f"{k}: {v}" for k, v in schema_strings]
            )
    except KeyError:
        # If the openapi does not have a schema section, we will just skip.
        pass

    # Add example input to the docstring if existing.
    try:
        example = api_info["requestBody"]["content"]["application/json"]["example"]
        example_string = "\n  ".join(
            [str(k) + ": " + str(v) for k, v in example.items()]
        )
        docstring += f"\n\nExample input:\n  {example_string}\n"
    except KeyError:
        # If the openapi does not have an example section, we will just skip.
        pass

    # Add output schema to the docstring.
    try:
        schema_ref = api_info["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        schema_name = schema_ref.split("/")[-1]
        schema = openapi["components"]["schemas"][schema_name]
        schema_strings = [
            (k, _json_to_type_string(v)) for k, v in schema["properties"].items()
        ]
        docstring += "\n\nOutput Schema:\n  " + "\n  ".join(
            [f"{k}: {v}" for k, v in schema_strings]
        )
    except KeyError:
        # If the openapi does not have a schema section, we will just skip.
        pass

    return docstring


def _get_positional_argument_error_message(
    openapi: Dict, path_name: str, args: Tuple
) -> str:
    POSITIONAL_ARGUMENT_ERROR_MESSAGE = (
        "Photon methods do not support positional arguments. If your client is named"
        f" `c`, Use `help(c.{path_name[1:]})` to see the function signature."
    )
    if openapi:
        try:
            additional_message = ""
            api_info, is_post = _get_api_info(openapi, path_name)
            if is_post:
                schema = _get_post_input_schema(openapi, api_info)
                if len(args) <= len(schema["properties"]):
                    additional_message += (
                        "\n\nIt seems that you have passed in positional arguments for"
                        f" the path `{path_name}`:\n    {args}\nDid you mean the"
                        f" following?\n    {path_name[1:]}(\n"
                    )
                    # Note: this actually assumes that the schema['properties'] is ordered, which luckily seems to be
                    # the case for now.
                    for i in range(len(args)):
                        additional_message += (
                            "       "
                            f" {list(schema['properties'].keys())[i]}={repr(args[i])},\n"
                        )
                    additional_message += "    )\n"
                    additional_message += (
                        "(while we try to be helpful, this is just a guess, and may not"
                        " be correct)"
                    )
        except KeyError:
            # Fallback option: if the best-effort guess of the proper calling format
            # fails, we will just use the default message.
            additional_message = ""
        POSITIONAL_ARGUMENT_ERROR_MESSAGE += additional_message
    return POSITIONAL_ARGUMENT_ERROR_MESSAGE


def get_file_content(
    src: Union[FileParam, str, bytes], allow_local_file: bool = False
) -> bytes:
    """
    Gets the file content from a source.

    The source can be one of the following: a FileParam object, a url, a local file path,
    a base64-encoded string, or raw bytes. For a base64-encoded string,
    we support two formats: (1) raw base64-encoded string, or (2) a string that starts with
    "data:" and contains a base64-encoded string, conforming to the RFC 2397 standard.

    Local file path is only supported if allow_local_file is True. This is for safety reasons,
    as we do not want to accidentally read files from the local file system when running a
    remote service.

    Inputs:
        src: the source of the file content.
    Returns:
        content: the file content in bytes.
    """
    if isinstance(src, FileParam):
        return src.content
    elif isinstance(src, str):
        if _is_valid_url(src):
            # If the source is a valid url, we will download the content and return it.
            try:
                content = requests.get(src).content
            except Exception:
                raise ValueError(f"Failed to download content from url: {src}")
            return content
        elif os.path.exists(src) and allow_local_file:
            with open(src, "rb") as f:
                return f.read()
        elif src.startswith("data:"):
            # Extract the leading base64 string, and decode it.
            # See https://tools.ietf.org/html/rfc2397 for the RFC 2397 standard.
            # Note: we will ignore the media type and encoding, as we only support
            # base64 encoding.
            pattern = re.compile(r"data:.*?;base64,(.*)")
            match = pattern.match(src)
            if match:
                try:
                    return base64.b64decode(match.group(1))
                except Exception:
                    raise ValueError(
                        "Invalid base64 string:"
                        f" {src if len(src) < 100 else src[:100] + '...'}"
                    )
        elif len(src) % 4 == 0 and re.match(r"^[A-Za-z0-9+/]*={0,2}$", src):
            # Last resort: we will try to decode the string as a base64 string.
            try:
                return base64.b64decode(src)
            except Exception:
                raise ValueError(
                    "Failed to decode base64 string:"
                    f" {src if len(src) < 100 else src[:100] + '...'}"
                )
        # If any of the above fails, we will raise an error.
        raise ValueError(
            "Failed to get file content from source:"
            f" {src if len(src) < 100 else src[:100] + '...'}"
        )
    elif isinstance(src, bytes):
        # Fallback: if the content is already bytes, do nothing.
        return src
    # Anything not covered above is not supported.
    raise TypeError(
        "src must be a FileParam, a url, a local file path, a base64-encoded string,"
        f" or raw bytes. Got {type(src)}"
    )
