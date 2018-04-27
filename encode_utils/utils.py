# -*- coding: utf-8 -*-

###
# © 2018 The Board of Trustees of the Leland Stanford Junior University
# Nathaniel Watson
# nathankw@stanford.edu
###

"""
Contains utilities that don't require authorization on the DCC servers.
"""

import hashlib
import json
import jsonschema
import logging
import os
import requests
import subprocess

import encode_utils as eu


#: Stores the HTTP headers to indicate JSON content in a request.
REQUEST_HEADERS_JSON = {'content-type': 'application/json'}

