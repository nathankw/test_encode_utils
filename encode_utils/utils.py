# -*- coding: utf-8 -*-

###
# © 2018 The Board of Trustees of the Leland Stanford Junior University
# Nathaniel Watson
# nathankw@stanford.edu
###

"""
Contains utilities that don't require authorization on the DCC servers.
"""

import json
import logging
import os
import requests
import subprocess
import pdb

import encode_utils as eu


REQUEST_HEADERS_JSON = {'content-type': 'application/json'}

#: A descendent logger of the debug logger created in `encode_utils`
#: (see the function description for `encode_utils.create_debug_logger`)
DEBUG_LOGGER = logging.getLogger(eu.DEBUG_LOGGER_NAME + "." + __name__)
#: A descendent logger of the error logger created in `encode_utils`
#: (see the function description for `encode_utils.create_error_logger`)
ERROR_LOGGER = logging.getLogger(eu.ERROR_LOGGER_NAME + "." + __name__)

class MD5SumError(Exception):
  """Raised when there is a non-zero exit status from the md5sum utility from GNU coreutils.
  """

class UnknownProfile(Exception):
  """
  Raised when the profile in question doesn't match any valid profile name present in
  """
  pass

def calculate_md5sum(file_path):
  """"Calculates the md5sum for a file using the md5sum utility from GNU coreutils.

  Args:
      file_path: str. The path to a local file.

  Returns:
      str: The md5sum.

  Raises:
      MD5SumError: There was a non-zero exit status from the md5sum command.
  """
  cmd = "md5sum {}".format(file_path)
  self.debug_logger.debug("Calculating md5sum for '{}' with command '{}'.".format(file_path,cmd))
  popen = subprocess.Popen(cmd,shell=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  stdout,stderr = popen.communicate()
  stdout = stdout.decode("utf-8")
  stderr = stderr.decode("utf-8")
  retcode = popen.returncode
  if retcode:
    error_msg = "Failed to calculate md5sum for file '{}'.".format(file_path)
    self.debug_logger.debug(error_msg)
    self.error_logger.error(error_msg)
    error_msg += (" Subprocess command '{cmd}' failed with return code '{retcode}'."
                  " Stdout is '{stdout}'.  Stderr is '{stderr}'.").format(
                    cmd=cmd,retcode=retcode,stdout=stdout,stderr=stderr)
    self.debug_logger.debug(error_msg)
    raise MD5SumError(error_msg)
  self.debug_logger.debug(stdout)
  return stdout

def get_profiles():
  """Creates a list of the profile IDs spanning all public profiles on the Portal.

  The profile ID for a given profile is extracted from the profile's `id` property, after a little
  formatting first.  The formatting works by removing the 'profiles' prefix and the '.json' suffix.
  For example, the value of the 'id' property for the genetic modification profile is
  `/profiles/genetic_modification.json`. The value that gets inserted into the list returned by
  this function is `genetic_modification`.

  Returns:
      list: list of profile IDs.
  """
  profiles = requests.get(eu.PROFILES_URL + "?format=json",
                          timeout=eu.TIMEOUT,
                          headers=REQUEST_HEADERS_JSON)
  profiles = profiles.json()
  return profiles

def get_profile_ids():
  """Creates a list of the profile IDs spanning all public profiles on the Portal.

  The profile ID for a given profile is extracted from the profile's `id` property, after a little
  formatting first.  The formatting works by removing the 'profiles' prefix and the '.json' suffix.
  For example, the value of the 'id' property for the genetic modification profile is
  `/profiles/genetic_modification.json`. The value that gets inserted into the list returned by
  this function is `genetic_modification`.

  Returns:
      list: list of profile IDs.
  """
  profiles = get_profiles()
  profile_ids = []
  for profile_name in profiles:
     if profile_name.startswith("_"):
       #i.e. _subtypes
       continue
     print(profile_name)
     profile_id = profiles[profile_name]["id"].split("/")[-1].split(".json")[0]
     profile_ids.append(profile_id)
  return profile_ids

class Profile:
  """
  Encapsulates knowledge about the existing profiles on the Portal and contains useful methods
  for working with a given profile.

  The user supplies a profile name, typically the value of a record's `@id` property. It will be
  normalized to match the syntax of the profile IDs in the list returned by the function
  `get_profile_ids()`.
  """
  profiles = requests.get(eu.PROFILES_URL + "?format=json",
                          timeout=eu.TIMEOUT,
                          headers=REQUEST_HEADERS_JSON).json()
  private_profile_names = [x for x in profiles if x.startswith("_")] #i.e. _subtypes.
  for i in private_profile_names:
    profiles.pop(i)
  del private_profile_names

  profile_ids = []
  awardless_profile_ids = []
  for profile_name in profiles:
    profile = profiles[profile_name]
    profile_id = profile["id"].split("/")[-1].split(".json")[0]
    profile_ids.append(profile_id)
    if eu.AWARD_PROP_NAME not in profile["properties"]:
      awardless_profile_ids.append(profile_id)

  #: The list of the profile IDs spanning all public profiles on the Portal, as returned by
  #: `get_profile_ids()`.
  PROFILE_IDS = profile_ids
  del profile_ids

  #: List of profile IDs that don't have the 'award' and 'lab' properties.
  AWARDLESS_PROFILES = awardless_profile_ids
  del awardless_profile_ids

  FILE_PROFILE_NAME = "file"
  try:
    assert(FILE_PROFILE_NAME in PROFILE_IDS)
  except AssertionError:
    print("WARNING: The profile for file.json has underwent a name change apparently and is no longer known to this package.")

  def __init__(self,profile_id):
    """
    Args:
        profile_id: str. Typically the value of a record's `@id` property.
    """

    #: The normalized version of the passed-in profile_id to the constructor. The normalization
    #: is neccessary in order to match the format of the profile IDs in the list Profile.PROFILE_IDS.
    self.profile_id = self._set_profile_id(profile_id)

  def _set_profile_id(self,profile_id):
    """
    Normalizes profile_id so that it matches the format of the profile IDs in the list
    Profile.PROFILE_IDS, and ensures that the normalized profile ID is a member of this list.

    Args:
        profile_id: str. Typeically the value of a record's `@id` property.

    Returns:
        str: The normalized profile ID.
    Raises:
        UnknownProfile: The normalized profile ID is not a member of the list Profile.PROFILE_IDS.
    """
    orig_profile = profile_id
    profile_id = profile_id.strip("/").split("/")[0].lower()
    #Multi-word profile names are hypen-separated, i.e. genetic-modifications.
    profile_id = profile_id.replace("-","")
    if not profile_id in Profile.PROFILE_IDS:
      profile_id = profile_id.rstrip("s")
      if not profile_id in Profile.PROFILE_IDS:
        raise UnknownProfile("Unknown profile ID '{}'.".format(orig_profile))
    return profile_id

  def get_schema(self):
    """Retrieves the JSON schema of the profile from the Portal.

    Returns:
        tuple: Two-item tuple where the first item is the URL used to fetch the schema, and the
            second item is a dict representing the profile's JSON schema.

    Raises:
        requests.exceptions.HTTPError: The status code is not okay.
    """
    url = os.path.join(eu.PROFILES_URL,self.profile_id + ".json?format=json")
    res = requests.get(url,headers=REQUEST_HEADERS_JSON,timeout=eu.TIMEOUT)
    res.raise_for_status()
    return url, res.json()
