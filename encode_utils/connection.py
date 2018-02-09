# -*- coding: utf-8 -*-

###
# © 2018 The Board of Trustees of the Leland Stanford Junior University
# Nathaniel Watson
# nathankw@stanford.edu
###

import base64
import datetime
import json
import logging
import mimetypes
import os
import pdb
import re
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import subprocess
import sys
import time
import urllib
import urllib3

#inhouse libraries
import encode_utils as eu
import encode_utils.profiles as eup
import encode_utils.utils as euu



LOG_DIR = "EU_Logs"

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
#urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class AwardPropertyMissing(Exception):
  """
  Raised when the 'award' property isn't set in the payload when doing a POST, and a default isn't
  set by the environment variable DCC_AWARD either.
  """
  message = ("The property '{}' is missing from the payload and a default isn't set either. To"
             " store a default, set the DCC_AWARD environment variable.")


class FileUploadFailed(Exception):
  """
  Raised when the AWS CLI returns a non-zero exit status.
  """


class LabPropertyMissing(Exception):
  """
  Raised when the 'lab' property isn't set in the payload when doing a POST, and a default isn't
  set by the environment variable DCC_AWARD either.
  """
  message = ("The property '{}' is missing from the payload and a default isn't set either. To"
             " store a default, set the DCC_LAB environment variable.")

class ProfileNotSpecified(Exception):
  """
  Raised when the profile (object schema) to submit to isn't specifed in a payload.
  """
  pass


class RecordIdNotPresent(Exception):
  """
  Raised when a payload to submit to the Portal doesn't have any record identifier (either
  a pre-existing ENCODE assigned identifier or an alias.
  """
  pass


class RecordNotFound(Exception):
  """
  Raised when a record that should exist on the Portal can't be retrieved via a GET request.
  """
  pass


class Connection():
  """ENCODE Portal data submission and retrieval.

  In order to authenticate with the DCC servers when making HTTP requests, you must have the
  the environment variables DCC_API_KEY and DCC_SECRET_KEY set. Check with your DCC data wrangler
  if you haven't been assigned these keys.

  Two log files will be opened in append mode in the calling directory, and named
  DCC_MODE_posted.txt and DCC_MODE_error.txt, where DCC_MODE represents the value stored in
  eu.DCC_MODE.
  """

  #: Identifies the name of the key in the payload (dictionary) that stores a valid ENCODE-assigned
  #: identifier for a record, such as 'accession', 'uuid', 'md5sum', ... depending on the object
  #: being submitted.
  #: This is not a valid attribute of any ENCODE object schema, and is only used in the patch()
  #: instance method when you need to designate the record to update and don't have an alias you
  #: can specify in the 'aliases' attribute.
  ENCID_KEY = "_enc_id"

  #: Identifies the name of the key in the payload (dictionary) that stores the ID of the profile
  #: to submit to.
  PROFILE_KEY = "_profile"

  POST = "post"
  PATCH = "patch"

  def __init__(self,dcc_mode=False):

    #: A reference to the debug logger that was created earlier; see `encode_utils.debug_logger`.
    #: This class adds a file handler, such that all messages send to it are logged to this
    #: file in addition to STDOUT>
    self.debug_logger = logging.getLogger(eu.DEBUG_LOGGER_NAME)
    self.dcc_mode = self._set_dcc_mode(dcc_mode)
    self.dcc_host = eu.DCC_MODES[self.dcc_mode]["host"]
    self.dcc_url = eu.DCC_MODES[self.dcc_mode]["url"]
   
    #Add debug file handler to debug_logger:
    self._add_file_handler(logger=self.debug_logger,level=logging.DEBUG,tag="debug")

    #: A `logging` instance with a file handler for logging messages at the ERROR level or greater.
    #: Meant to log terse error messages.
    #: The log file resides locally within the directory specified by the constant LOG_DIR.
    self.error_logger = logging.getLogger(eu.ERROR_LOGGER_NAME)
    log_level = logging.ERROR
    self.error_logger.setLevel(log_level)
    self._add_file_handler(logger=self.error_logger,level=log_level,tag="error")

    #: A `logging` instance with a file handler for logging successful POST operations.
    #: The log file resides locally within the directory specified by the constant LOG_DIR.
    #: Accepts messages >= logging.INFO.
    self.post_logger = logging.getLogger(eu.POST_LOGGER_NAME)
    log_level = logging.INFO
    self.post_logger.setLevel(log_level)
    self._add_file_handler(logger=self.post_logger,level=log_level,tag="posted")


    #: The API key to use when authenticating with the DCC servers. This is set automatically
    #: to the value of the DCC_API_KEY environment variable in the _set_api_keys() private method.
    self.api_key = self._set_api_keys()[0]
    #: The secret key to use when authenticating with the DCC servers. This is set automatically
    #: to the value of the DCC_SECRET_KEY environment variable in the _set_api_keys() private method.
    self.secret_key = self._set_api_keys()[1]
    self.auth = (self.api_key,self.secret_key)

  def _set_dcc_mode(self,dcc_mode=False):
    if not dcc_mode:
      try:
        dcc_mode = os.environ["DCC_MODE"]
        self.debug_logger.debug("Utilizing DCC_MODE environment variable.")
      except KeyError:
        raise Exception("You must supply the `dcc_mode` argument or set the environment variable DCC_MODE.")
    dcc_mode = dcc_mode.lower()
    if dcc_mode not in eu.DCC_MODES:
      raise Exception(
        "The specified dcc_mode of '{}' is not valid. Should be one of '{}' or '{}'.".format(dcc_mode, eu.DCC_MODES.keys()))
    return dcc_mode

  def _get_logfile_name(self,log_level,tag):
    if not os.path.exists(LOG_DIR):
      os.mkdir(LOG_DIR)
    filename = "log_eu_" + self.dcc_mode + "_" + tag + ".txt"
    filename = os.path.join(LOG_DIR,filename)
    return filename

  def _add_file_handler(self,logger,level,tag):
    """
    Creates a logger that logs messages at the ERROR level or greater. There is a single handler,
    which logs its messages to a file by the name of log_eu_$DCC_MODE_error.txt.
    """
    f_formatter = logging.Formatter('%(asctime)s:%(name)s:\t%(message)s')
    filename = self._get_logfile_name(level,tag)
    handler = logging.FileHandler(filename=filename,mode="a")
    handler.setLevel(level)
    handler.setFormatter(f_formatter)
    logger.addHandler(handler)

  def _set_api_keys(self):
    """
    Retrieves the API key and secret key based on the environment variables DCC_API_KEY and
    DCC_SECRET_KEY.

    Returns:
        `tuple`: Two item tuple containing the API Key and the Secret Key
    """
    api_key = os.environ["DCC_API_KEY"]
    secret_key = os.environ["DCC_SECRET_KEY"]
    return api_key,secret_key

  def _log_post(self,alias,dcc_id):
    """Uses the self.post_logger to log the submitted object's alias and dcc_id.

    Each message is written in a two column format delimted by a tab character. The columns are:
      1) alias (the first that appeared in the 'aliases' key in the payload), and
      2) DCC identifier
    """
    entry = alias + "\t" + dcc_id
    self.post_logger.info(entry)

  def get_aliases(self,dcc_id,strip_alias_prefix=False):
    """
    Given an ENCODE identifier for an object, performs a GET request and extracts the aliases.

    Args:
        dcc_id: `str`. The ENCODE ID for a given object, i.e ENCSR999EHG.
        strip_alias_prefix: `bool`. True means to remove the alias prefix if all return aliases.

    Returns:
        `list`: The aliases.
    """
    record = self.get(ignore404=False,dcc_id=dcc_id)
    aliases = record["aliases"]
    for index in range(len(aliases)):
      alias = aliases[index]
      if strip_alias_prefix:
        aliases[index] =  euu.strip_alias_prefix(alias)
    return aliases

  def make_search_url(self,search_args,limit=None):
    """Creates a URL encoded URL given the search arguments.

    Args:
        search_args: `dict`. The key and value query parameters.
        limit: `int`. The number of search results to return. Don't specify if you want all. 

    Returns:
        `str`: The URL containing the URL encoded query.

    Raises:
        requests.exceptions.HTTPError: The status code is not in the set [200,404].
    """
    if not limit:
      search_args["limit"] = "all"
    else:
      search_args["limit"] = str(limit)

    #Convert dict to list of two-item tuples since order of search arguments will be preserved 
    # this way per the documentation (easier for the corresponding test case).
    search = sorted(search_args.items())
    query = urllib.parse.urlencode(search)
    url = os.path.join(self.dcc_url,"search/?") + query
    return url

  def search(self,search_args,limit=None):
    """
    Searches the Portal using the provided query parameters,which will first be URL encoded.

    Args:
        search_args: `dict`. The key and value query parameters.
        limit: `int`. The number of search results to return. Don't specify if you want all. 

    Returns:
        `list`: The search results.

    Raises:
        requests.exceptions.HTTPError: The status code is not in the set [200,404].

    Example:
        Given we have the following dictionary *d* of key and value pairs::

            {"type": "experiment",
             "searchTerm": "ENCLB336TVW",
             "format": "json",
             "frame": "object",
             "datastore": "database"
            }

        We can call the function as::

            search_encode(search_args=d)

    """
    url = self.make_search_url(search_args=search_args,limit=limit)
    self.debug_logger.debug("Searching DCC with query {url}.".format(url=url))
    response = requests.get(url,
                            auth=self.auth,
                            timeout=eu.TIMEOUT,
                            headers=euu.REQUEST_HEADERS_JSON,
                            verify=False)
    if response.status_code not in [requests.codes.OK,requests.codes.NOT_FOUND]:
      response.raise_for_status()
    return response.json()["@graph"] #the @graph object is a list


  def get_profile_from_payload(self,payload):
    """
    Useful to call when doing a POST (and self.post() does call this). Ensures that the profile key
    identified by self.PROFILE_KEY exists in the passed-in payload and that the value is
    a recognized ENCODE object profile (schema). Alternatively, the user can set the profile in 
    the more convoluted `@id` attribute.

    Args:
        payload: `dict`. The intended object data to POST.

    Returns:
        `str`: The ID of the profile if all validations pass, otherwise.

    Raises:
        encode_utils.connection.ProfileNotSpecified: Both keys self.PROFILE_KEY and `@id` are
          missing in the payload.
        encode_utils.profiles.UnknownProfile: The profile ID isn't recognized by the class
            `encode_utils.profiles.Profile`.
    """

    profile_id = payload.get(self.PROFILE_KEY)
    if not profile_id:
      profile_id = payload.get("@id")
      if not profile_id:
        raise ProfileNotSpecified(
          ("You need to specify the ID of the profile to submit to by using the '{}' key"
           " in the payload, or by setting the `@id` property explicitely.").format(self.PROFILE_KEY))
    profile = eup.Profile(profile_id) #raises euu.UnknownProfile if unknown profile ID.
    return profile.profile_id

  def get_lookup_ids_from_payload(self,payload):
    """
    Given a payload to submit to the Portal, extracts the identifiers that can be used to lookup
    the record on the Portal, i.e. to see if the record already exists. Identifiers are extracted
    from the following fields:
    1) self.ENCID_KEY,
    2) aliases,
    3) md5sum (in the case of a file object)

    Args:
        payload: `dict`. The data to submit.

    Returns:
        `list`: The possible lookup identifiers.
    """
    lookup_ids = []
    if self.ENCID_KEY in payload:
      lookup_ids.append(payload[self.ENCID_KEY])
    if "aliases" in payload:
      lookup_ids.extend(payload["aliases"])
    if "md5sum" in payload:
      #The case for file objects.
      lookup_ids.append(payload["md5sum"])

    lookup_ids = [x.strip() for x in lookup_ids]
    lookup_ids = [x for x in lookup_ids]
    if not lookup_ids:
        raise RecordIdNotPresent(
          ("The payload does not contain a recognized identifier for traceability. For example,"
           " you need to set the 'aliases' key, or specify an ENCODE assigned identifier in the"
           " non-schematic key {}.".format(self.ENCID_KEY)))

    return lookup_ids

  #def delete(self,rec_id):
  #  """Not supported at present by the DCC - Only wranglers and delete objects.
  #  """
  #  url = os.path.join(self.dcc_url,rec_id)
  #  self.logger.info(
  #    (">>>>>>DELETING {rec_id} From DCC with URL {url}").format(rec_id=rec_id,url=url))
  #  response = requests.delete(url,auth=self.auth,timeout=eu.TIMEOUT,headers=euu.REQUEST_HEADERS_JSON, verify=False)
  #  pdb.set_trace()
  #  if response.ok:
  #    return response.json()
  #  response.raise_for_status()

  def get(self,rec_ids,ignore404=True,frame=None):
    """GET a record from the ENCODE Portal.

    Looks up a record in ENCODE and performs a GET request, returning the JSON serialization of
    the object. You supply a list of identifiers for a specific record, such as the object ID, an
    alias, uuid, or accession. The ENCODE Portal will be searched for each identifier in turn
    until one is either found or the list is exhaused.

    Args:
        rec_ids: `str` containing a single record identifier, or a list of identifiers for a
            specific record. For a few example identifiers, you can be a uuid, accession, ...,  
            or even the value of a record's `@id` property.
        ignore404: `bool`. Only matters when none of the passed in record IDs were found on the
            ENCODE Portal. In this case, If set to True, then an empty dict will be returned.
            If set to False, then an E


    Returns:
        `dict`: The JSON response. Will be empty if no record was found AND ignore404=True.

    Raises:
        requests.exceptions.HTTPError: The status code is not okay (in the 200 range), and the
            cause isn't due to a 404 (not found) status code when ignore404=True.
    """
    if isinstance(rec_ids,str):
      rec_ids = [rec_ids]
    status_codes = {} #key is return code, value is the record ID
    for r in rec_ids:
      if r.endswith("/"):
        r = r.rstrip("/")
      url = os.path.join(self.dcc_url,r,"?format=json&datastore=database")
      if frame:
        url += "&frame={frame}".format(frame=frame)
      self.debug_logger.debug(">>>>>>GETTING {rec_id} From DCC with URL {url}".format(
          rec_id=r,url=url))
      response = requests.get(url,
                              auth=self.auth,
                              timeout=eu.TIMEOUT,
                              headers=euu.REQUEST_HEADERS_JSON,
                              verify=False)
      if response.ok:
        return response.json()
      status_codes[response.status_code] = r

    if requests.codes.FORBIDDEN in status_codes:
      raise Exception(
        "Access to ENCODE record {} is forbidden".format(status_codes[requests.codes.FORBIDDEN]))
    elif requests.codes.NOT_FOUND in status_codes:
      if ignore404:
        return {}
    #At this point in the code, the response is not okay.
    # Raise the error for last response we got:
    response.raise_for_status()

  def set_attachment(self,document):
    """
    Sets the attachment property for any profile that supports it, such as document or 
    antibody_characterization.

    Args:
        document: `str`. A local file path.

    Returns:
        `dict`. The attachment propery value.
    """
    download_filename = os.path.basename(document)
    mime_type = mimetypes.guess_type(download_filename)[0]
    data = base64.b64encode(open(document,'rb').read())
    temp_uri = str(data,"utf-8")
    href = "data:{mime_type};base64,{temp_uri}".format(mime_type=mime_type,temp_uri=temp_uri)
    #download_filename = library_alias.split(":")[1] + "_relative_knockdown.jpeg"
    attachment = {}
    attachment["download"] = download_filename
    attachment["type"] = mime_type
    attachment["href"] = href
    return attachment

  def after_submit_file_cloud_upload(self,rec_id,profile_id):
    """An after-POST submit hook for uploading files to AWS.

    Some objects, such as Files (file.json profile) need to have a corresponding file in the cloud.
    Where in the cloud the actual file should be uploaded to is indicated in File object's
    file.upload_credentials.upload_url property. Once the File object is posted, this hook can be
    used to perform the actual cloud upload of the physical, local file reprented by the File object.

    Args:
        rec_id: `str`. An identifier for the new File object on the Portal.
        profile_id: `str`. The ID of the profile that the record belongs to.
    """
    if profile_id != eup.Profile.FILE_PROFILE_ID:
      return
    rec = self.get(rec_ids=rec_id,ignore404=False)
    if eup.Profile.SUBMITTED_FILE_PROP_NAME in rec:
      filename = rec[eup.Profile.SUBMITTED_FILE_PROP_NAME]
      if filename:
        self.upload_file(file_id=rec_id,file_path=filename)

  def after_submit_hooks(self,rec_id,profile_id,method=""):
    """
    Calls after-submission hooks for POST and PATH operations.

    Args:
        rec_id: `str`. An identifier for a record on the Portal.
        profile_id: `str`. The profile the record belongs to.
        method: str. One of self.POST or self.PATCH, or the empty string to indicate which 
            registered hooks to look through.
    """
    #Check allowed_methods. Will matter later when there are POST-specific
    # and PATCH-specific hooks.
    allowed_methods = [self.POST,self.PATCH,""]
    if not method in allowed_methods:
      raise Exception("Unknown method '{}': must be one of {}.".format(method,allowed_methods))

    #Call agnostic hooks
    #... None yet.

    #Call POST-specific hooks if POST:
    if method == self.POST:
      self.after_submit_file_cloud_upload(rec_id,profile_id)

    #Call PATCH-specific hooks if PATCH:
    #... None yet.

  def before_submit_alias(self,payload):
    """
    A POST and PATCH pre-submit hook used to add the alias prefix to any aliases that are 
    missing it. An alias prefix is composed of the 

    Args:
        payload: `dict`. The payload to submit to the Portal.

    Returns:
        `dict`: The payload to submit to the Portal.
    """
    aliases_prop = "aliases"
    if not aliases_prop in payload:
      return payload
    payload[aliases_prop] = euu.add_alias_prefix(payload[aliases_prop])
    return payload
    

  def before_submit_attachment(self,payload):
    """
    A POST and PATCH pre-submit hook used to simplify the creation of an attachment in profiles 
    that support it.

    Checks the payload for the presence of the 'attachment' property that is used by certain 
    profiles, i.e. document and antibody_characterization, and then checks to see if a particular
    shortcut is being employed to indicate the attachment. That shortcut works as follows: If the 
    dictionary value of the 'attachment' key has a key named 'path' in it (case-sensitive), then 
    the value is taken to be the path to a local file. Then, the actual attachment object is 
    constructed, as defined in the document profile, by calling self.set_attachment(). Note that 
    this shortcut is particular to this `Connection` class, and when used the 'path' key should be 
    the only key in the attachment dictionary as any others would be ignored.

    Args:
        payload: `dict`. The payload to submit to the Portal.

    Returns:
        `dict`: The payload to submit to the Portal.
    """
    attachment_prop = "attachment"
    path = "path"

    if attachment_prop in payload:
      val = payload[attachment_prop] #dict
      if path in val:
        #Then set the actual attachment object:
        attachment = self.set_attachment(document=val[path])
        payload[attachment_prop] = attachment
    return payload

  def before_post_file(self,payload):
    """Calculates and sets the md5sum property for a file record.    

    Args:
        payload: `dict`. The payload to submit to the Portal.

    Returns:
        `dict`: The payload to submit to the Portal.

    Raises:
        encode_utils.utils.MD5SumError: Perculated through the function 
          `encode_utils.utils.calculate_md5sum` when it can't calculate the md5sum.
    """
    profile_id = self.get_profile_from_payload(payload)
    if profile_id != eup.Profile.FILE_PROFILE_ID:
      return payload
    try:
      file_name = payload[eup.Profile.SUBMITTED_FILE_PROP_NAME]
    except KeyError:
      return payload
    if eup.Profile.MD5SUM_NAME_PROP_NAME in payload:
      if payload[eup.Profile.MD5SUM_NAME_PROP_NAME]:
        #Already set; nothing to do.
        return payload
    md5sum = euu.calculate_md5sum(file_name)
    payload["md5sum"] = md5sum
    return payload


  def before_submit_hooks(self,payload,method=""):
    """Calls before-submission hooks for POST and PATCH operations.

    Some hooks only run if you are doing a PATCH, others if you are only doing a POST. Then there
    are some that run if you are doing either operation. Each pre-submission hook that is called
    can potentially modify the payload.

    Both self.post() and self.patch() call this method.

    Args:
        payload: `dict`. The payload to POST or PATCH.
        method: `str`. One of "post" or "patch", or the empty string to indicate which registered
            hooks to look through.

    Returns:
        `dict`: The potentially modified payload that has been passed through all applicable
        pre-submit hooks.
    """
    #Check allowed_methods. Will matter later when there are POST-specific
    # and PATCH-specific hooks.
    allowed_methods = [self.POST,self.PATCH,""]
    if not method in allowed_methods:
      raise Exception("Unknown method '{}': must be one of {}.".format(method,allowed_methods))

    #Call agnostic hooks
    payload = self.before_submit_attachment(payload)
    payload = self.before_submit_alias(payload)

    #Call POST-specific hooks if POST:
    if method == self.POST:
      payload = self.before_post_file(payload)

    #Call PATCH-specific hooks if PATCH:
    #... None yet.

    return payload


  def post(self,payload):
    """POST a record to the ENCODE Portal.

    Requires that you include in the payload the non-schematic key self.PROFILE_KEY to
    designate the name of the ENCODE object profile that you are submitting against, or the 
    actual `@id` property itself, which is rather convoluted. 

    If the 'lab' property isn't present in the payload, then the default will be set to the value
    of the DCC_LAB environment variable. Similarly, if the 'award' property isn't present, then the
    default will be set to the value of the DCC_AWARD environment variable.

    Before the POST is attempted, any pre-submit hooks are fist called (see the method
    `self.before_submit_hooks`).

    Args:
        payload: `dict`. The data to submit.

    Returns:
        `dict`: The JSON response from the POST operation, or GET operation If the resource already
        exist on the Portal.

    Raises:
        AwardPropertyMissing: The 'award' property isn't present in the payload and there isn't a
            defualt set by the environment variable DCC_AWARD.
        LabPropertyMissing: The 'lab' property isn't present in the payload and there isn't a
            default set by the environment variable DCC_AWARD.
        requests.exceptions.HTTPError: The return status is not okay (not in the 200 range), with
            the exception of a conflict (409), which is only logged.
    """
    self.debug_logger.debug("\nIN post().")
    #Make sure we have a payload that can be converted to valid JSON, and tuples become arrays, ...
    json.loads(json.dumps(payload))
    profile_id = self.get_profile_from_payload(payload)
    url = os.path.join(self.dcc_url,profile_id)
    #Check if we need to add defaults for 'award' and 'lab' properties:
    if profile_id not in eup.Profile.AWARDLESS_PROFILE_IDS: #No lab prop for these profiles either.
      if eu.AWARD_PROP_NAME not in payload:
        if not eu.AWARD:
          raise AwardPropertyMissing
        payload.update(eu.AWARD)
      if eu.LAB_PROP_NAME not in payload:
        if not eu.LAB:
          raise LabPropertyMissing
        payload.update(eu.LAB)
    alias = payload["aliases"][0]

    #Run 'before' hooks:
    payload = self.before_submit_hooks(payload,method=self.POST)
    #Remove the non-schematic self.PROFILE_KEY if being used. Also check for the '@id' property
    # and remove if found too.
    try:
      payload.pop(self.PROFILE_KEY)
    except KeyError:
      pass
    try:
      payload.pop("@id")
    except KeyError:
      pass

    self.debug_logger.debug(
        ("<<<<<< POSTING {alias} To DCC with URL {url} and this"
         " payload:\n\n{payload}\n\n").format(alias=alias,url=url,payload=euu.print_format_dict(payload)))

    response = requests.post(url,
                             auth=self.auth,
                             timeout=eu.TIMEOUT,
                             headers=euu.REQUEST_HEADERS_JSON,
                             json=payload, verify=False)
    #response_json = response.json()["@graph"][0]
    response_json = response.json()

    if response.ok:
      self.debug_logger.debug("Success.")
      response_json = response_json["@graph"][0]
      encid = ""
      try:
        encid = response_json["accession"]
      except KeyError:
        #Some objects don't have an accession, i.e. replicates.
        encid = response_json["uuid"]
      self._log_post(alias=alias,dcc_id=encid)
      #Run 'after' hooks:
      self.after_submit_hooks(encid,profile_id,method=self.POST)
      return response_json
    elif response.status_code == requests.codes.CONFLICT:
      log_msg = "Will not post {} because it already exists.".format(alias)
      self.debug_logger.debug(log_msg)
      self.error_logger.error(log_msg)
      rec_json = self.get(rec_ids=alias,ignore404=False)
      return rec_json
    else:
      message = "Failed to POST {alias}".format(alias=alias)
      self.debug_logger.debug(message)
      self.error_logger.error(message)
      self.debug_logger.debug("<<<<<< DCC POST RESPONSE: ")
      self.debug_logger.debug(euu.print_format_dict(response_json))
      response.raise_for_status()

  def patch(self,payload,raise_403=True, extend_array_values=True):
    """PATCH a record on the ENCODE Portal.

    Before the PATCH is attempted, any pre-submit hooks are fist called (see the method
    `self.before_submit_hooks`).

    Args:
        payload: `dict`. containing the attribute key and value pairs to patch. Must contain the key
            self.ENCID_KEY in order to indicate which record to PATCH.
        raise_403: `bool`. True means to raise a requests.exceptions.HTTPError if a 403 status
            (Forbidden) is returned.
            If set to False and there still is a 403 return status, then the object you were
            trying to PATCH will be fetched from the Portal in JSON format as this function's
            return value.
        extend_array_values: `bool`. Only affects keys with array values. True (default) means to
            extend the corresponding value on the Portal with what's specified in the payload.
            False means to replace the value on the Portal with what's in the payload.

    Returns:
        `dict`: The JSON response from the PATCH operation.

    Raises:
        KeyError: The payload doesn't have the key self.ENCID_KEY set AND there aren't
            any aliases provided in the payload's 'aliases' key.
        requests.exceptions.HTTPError: if the return status is not in the 200 range (excluding a
            403 status if 'raise_403' is False.
    """
    #Make sure we have a payload that can be converted to valid JSON, and tuples become arrays, ...
    json.loads(json.dumps(payload))
    self.debug_logger.debug("\nIN patch()")
    encode_id = payload[self.ENCID_KEY]
    rec_json = self.get(rec_ids=encode_id,ignore404=False)

    if extend_array_values:
      for key in payload:
        if isinstance(payload[key],list):
          val = payload[key]
          val.extend(rec_json.get(key,[]))
          #I use rec_json.get(key,[]) above because in a GET request,
          # not all props are pulled back when they are empty.
          # For ex, in a file object, if the controlled_by prop isn't set, then
          # it won't be in the response.
          payload[key] = list(set(val))

    #Run 'before' hooks:
    payload = self.before_submit_hooks(payload,method=self.PATCH)
    payload.pop(self.ENCID_KEY)

    url = os.path.join(self.dcc_url,encode_id)
    self.debug_logger.debug(
        ("<<<<<< PATCHING {encode_id} To DCC with URL"
         " {url} and this payload:\n\n{payload}\n\n").format(
             encode_id=encode_id,url=url,payload=euu.print_format_dict(payload)))

    response = requests.patch(url,auth=self.auth,timeout=eu.TIMEOUT,headers=euu.REQUEST_HEADERS_JSON,
                              json=payload,verify=False)
    response_json = response.json()

    if response.ok:
      self.debug_logger.debug("Success.")
      response_json = response_json["@graph"][0]
      uuid = response_json["uuid"]
      profile = eup.Profile(response_json["@id"])
      #Run 'after' hooks:
      self.after_submit_hooks(uuid,profile.profile_id,method=self.PATCH)
      return response_json
    elif response.status_code == requests.codes.FORBIDDEN:
      #Don't have permission to PATCH this object.
      if not raise_403:
        return rec_json

    message = "Failed to PATCH {}".format(encode_id)
    self.debug_logger.debug(message)
    self.error_logger.error(message)
    self.debug_logger.debug("<<<<<< DCC PATCH RESPONSE: ")
    self.debug_logger.debug(euu.print_format_dict(response_json))
    response.raise_for_status()


  def send(self,payload,error_if_not_found=False,extend_array_values=True,raise_403=True):
    """
    A wrapper over self.post() and self.patch() that determines which to call based on whether the
    record exists on the Portal or not. Especially useful when submitting a high-level object,
    such as an experiment which contains many dependent objects, in which case you could have a mix
    where some need to be POST'd and some PATCH'd.

    Args:
        payload: `dict`. The data to submit.
        error_if_not_found: `bool`. If set to True, then a PATCH will be attempted and a
            requests.exceptions.HTTPError will be raised if the record doesn't exist on the Portal.
        extend_array_values: `bool`. Only matters when doing a PATCH, and Only affects keys with
            array values. True (default) means to extend the corresponding value on the Portal
            with what's specified in the payload. False means to replace the value on the Portal
            with what's in the payload.
        raise_403: `bool`. Only matters when doing a PATCH. True means to raise an
            requests.exceptions.HTTPError if a 403 status (Forbidden) is returned.
            If set to False and there still is a 403 return status, then the object you were
            trying to PATCH will be fetched from the Portal in JSON format as this function's
            return value (as handled by self.patch()).

    Raises:
          requests.exceptions.HTTPError: You want to do a PATCH (indicated by setting
              error_if_not_found=True) but the record isn't found.
    """
    #Check wither record already exists on the portal
    lookup_ids = self.get_lookup_ids_from_payload(payload)
    rec_json = self.get(rec_ids=lookup_ids,ignore404=not error_if_not_found)

    if not rec_json:
      return self.post(payload=payload)
    else:
      #PATCH
      if self.ENCID_KEY not in payload:
        encode_id = aliases[0]
        payload[self.ENCID_KEY] = encode_id
      return self.patch(payload=payload,extend_array_values=extend_array_values,raise_403=raise_403)

  def get_fastqfile_replicate_hash(self,dcc_exp_id):
    """
    Given a DCC experiment ID, looks in the 'original' property to find FASTQ file objects and
    creates a dict organized by replicate numbers. Keying through the dict by replicate numbers,
    you can get to a particular file object's JSON serialization.

    Args:
        dcc_exp_id: `list` of DCC file IDs or aliases
    Returns:
        `dict`: `dict` where each key is a biological_replicate_number.
        The value of each key is another dict where each key is a technical_replicate_number.
        The value of this is yet another dict with keys being file read numbers -
        1 for forward reads, 2 for reverse reads.  The value
        for a given key of this most inner dictionary is a list of file objects.
    """
    exp_json = self.get(ignore404=False,rec_ids=dcc_exp_id)
    dcc_file_ids = exp_json["original_files"]
    dico = {}
    for i in dcc_file_ids:
      file_json = self.get(ignore404=False,rec_ids=i)
      if file_json["file_type"] != "fastq":
        continue #this is not a file object for a FASTQ file.
      brn = file_json["replicate"]["biological_replicate_number"]
      trn = file_json["replicate"]["technical_replicate_number"]
      read_num = file_json["paired_end"] #string
      if brn not in dico:
        dico[brn] = {}
      if trn not in dico[brn]:
        dico[brn][trn] = {}
      if read_num not in dico[brn][trn]:
        dico[brn][trn][read_num] = []
      dico[brn][trn][read_num].append(file_json)
    return dico

  def extract_aws_upload_credentials(self,file_json):
    """
    Sets values for the AWS environment variables to the credentials found in a file record's 
    `upload_credentials` property.

    Args:
        file_json: `dict`: A file record's JSON serialization.
 
    Returns:
        `dict`: `dict` containing keys named after AWS environment variables being:

          1. AWS_ACCESS_KEY_ID,
          2. AWS_SECRET_ACCESS_KEY,
          3. AWS_SECURITY_TOKEN,
          4. UPLOAD_URL

        Will be empty if the `upload_credentials` property isn't present in `file_json`.
    """
    try:
      creds = file_json["upload_credentials"]
    except KeyError:
      return {}
    aws_creds = {}
    aws_creds["AWS_ACCESS_KEY_ID"] = creds["access_key"]
    aws_creds["AWS_SECRET_ACCESS_KEY"] = creds["secret_key"]
    aws_creds["AWS_SECURITY_TOKEN"] = creds["session_token"]
    aws_creds["UPLOAD_URL"] = creds["upload_url"]
    return aws_creds

  def set_aws_upload_config(self,file_id):
    """
    Sets the AWS security credentials needed to upload a file to AWS S3 by the
    AWS CLI agent. First will attempt to extract the upload credentials
    from the file record if the property `upload_credentials` is set. If not set, then an attempt
    to regenerate the upload credentials will be made. 

    Args:
        file_id: `str`. A file object identifier (i.e. accession, uuid, alias, md5sum).

    Returns:
        `dict`: See documentation for the return value for self.extract_aws_upload_credentials().
    """
    file_json = self.get(file_id,ignore404=False)
    creds = self.set_aws_upload_config(file_json)
    if not creds:
      creds = self.regenerate_aws_upload_creds(file_id)
      #Will be None if forbidden.

    if not creds:
      return {}

    #URL example from dev Portal:
    #  s3://encoded-files-dev/2018/01/28/7c5c6d58-c98a-48b4-9d4b-3296b4126b89/TSTFF334203.fastq.gz"
    #  That's the uuid after the date.
    return creds

  def regenerate_aws_upload_creds(self,file_id):
    """Reissues AWS S3 upload credentials for the specified file object.

    Args:
        file_id: `str`. An identifier for a file object on the Portal.

    Returns:
        `dict`: `dict` containing the value of the 'upload_credentials' key in the JSON serialization
        of the file object represented by file_id. Will be empty if new upload credentials
        could not be issued.
    """
    self.debug_logger.debug("Using curl to generate new file upload credentials")
    cmd = ("curl -X POST -H 'Accept: application/json' -H 'Content-Type: application/json'"
           " https://{api_key}:{secret_key}@{host}/files/{file_id}/upload -d '{{}}'"
           " | python -m json.tool").format(api_key=self.api_key,secret_key=self.secret_key,host=self.dcc_host,file_id=file_id)
    self.debug_logger.debug("curl command: '{}'".format(cmd))
    popen = subprocess.Popen(cmd,shell=True,stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout,stderr = popen.communicate() #each is a bytes object.
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    retcode = popen.returncode
    if retcode:
      raise Exception(("Command {cmd} failed with return code {retcode}. stdout is {stdout} and"
                       " stderr is {stderr}.").format(cmd=cmd,retcode=retcode,stdout=stdout, stderr=stderr))
    response = json.loads(stdout)
    self.debug_logger.debug(response)
    if "code" in response:
      #Then problem occurred.
      code = response["code"]
      self.error_logger.error("Unable to reissue upload credentials for {}: Code {}.".format(file_id,code))
      return {}

      # For ex, response would look like this for a 404.

      # {
      #     "@type": [
      #         "HTTPNotFound",
      #         "Error"
      #     ],
      #     "code": 404,
      #     "description": "The resource could not be found.",
      #     "detail": "/files/michael-snyder:test_file_1/upload",
      #     "status": "error",
      #     "title": "Not Found"
      # }

      # You get a 403 when the 'status' of the file object isn't set to 'uploading'.
      # You also get this when the file object no-longer has read access (was archived by wranglers).

    graph = response["@graph"][0]
    return response["@graph"][0]["upload_credentials"]

  def upload_file(self,file_id,file_path=None):
    """Uses AWS CLI to upload a local file or S3 object to the Portal for the indicated file object.

    Unfortunately, it doesn't appear that pulling a file into S3 is supported through the AWS API;
    only existing S3 objects or local files can be copied to a S3 bucket. External files must first
    be downloaded and then pushed to the S3 bucket.

    Args:
        file_id: 'str'. An identifier of a `file` record.
        file_path: `str`. the local path to the file to upload, or an S3 object (i.e s3://mybucket/test.txt).
          If not set, defaults to None in which case the local file path will be extracted from the
          record's `submitted_file_name` property.

    Raises:
        FileUploadFailed: The return code of the AWS upload command was non-zero.
    """
    self.debug_logger.debug("\nIN upload_file()\n")
    aws_creds = self.set_aws_upload_config(file_id)
    if not aws_creds:
      msg = "Cannot upload file for {} since upload credentials could not be generated.".format(file_id)
      self.debug_logger.debug(msg)
      self.error_logger.error(msg)
      return
    if not file_path:
      file_rec = self.get(rec_ids=file_id)
      try:
        file_path = file_rec[eup.Profile.SUBMITTED_FILE_PROP_NAME]
      except KeyError: #subbmited_file_name property not set:
        pass
      if not file_path:
        raise Exception("No file path specified.")

    cmd = "aws s3 cp {file_path} {upload_url}".format(file_path=file_path,upload_url=aws_creds["UPLOAD_URL"])
    self.debug_logger.debug("Running command {cmd}.".format(cmd=cmd))
    popen = subprocess.Popen(cmd,
                             shell=True,
                             env=os.environ.update(aws_creds),
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    stdout,stderr = popen.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    retcode = popen.returncode
    if retcode:
      error_msg = "Failed to upload file '{}' for {}.".format(file_path,file_id)
      self.debug_logger.debug(error_msg)
      self.error_logger.error(error_msg)
      error_msg += (" Subprocess command '{cmd}' failed with return code '{retcode}'."
                    " Stdout is '{stdout}'.  Stderr is '{stderr}'.").format(
                      cmd=cmd,retcode=retcode,stdout=stdout,stderr=stderr)
      self.debug_logger.debug(error_msg)
      raise FileUploadFailed(error_msg)
    self.debug_logger.debug("AWS upload successful.")


  def get_platforms_on_experiment(self,rec_id):
    """
    Looks at all FASTQ files on the specified experiment, and tallies up the varying sequencing
    platforms that generated them.  This is moreless used to verify that there aren't a mix of
    multiple different platforms present as normally all reads should come from the same platform.

    Args:
        rec_id: `str`. DCC identifier for an experiment.
    Returns:
        `list`: The de-duplicated list of platforms seen on the experiment's FASTQ files.
    """
    exp_json = self.get(rec_ids=rec_id,frame=None)
    files_json = exp_json["original_files"]
    platforms = []
    for f in files_json:
      if not f["file_format"] == "fastq":
        continue
      platforms.extend(f["platform"]["aliases"])
    return list(set(platforms))

  def post_document(self,download_filename,document,document_type,description):
    """POSTS a document to the Portal.

    The alias for the document will be the lab prefix plus the file name.

    Args:
        download_filename: `str`. The name to give the document when downloading it from the ENCODE
          portal.
        document_type: `str`. For possible values, see
          https://www.encodeproject.org/profiles/document.json. It appears that one should use
          "data QA" for analysis results documents.
        description: `str`. The description for the document.
        document: `str`. Local file path to the document to be submitted.

    Returns:
        `str`: The DCC UUID of the new document.
    """
    document_filename = os.path.basename(document)
    document_alias = eu.LAB[eu.LAB_PROP_NAME] + ":" + document_filename
    mime_type = mimetypes.guess_type(document_filename)[0]
    if not mime_type:
      raise Exception("Couldn't guess MIME type for {}.".format(document_filename))

    ## Post information
    payload = {}
    payload[self.PROFILE_KEY] = "document"
    payload["aliases"] = [document_alias]
    payload["document_type"] = document_type
    payload["description"] = description

    #download_filename = library_alias.split(":")[1] + "_relative_knockdown.jpeg"
    attachment = self.set_attachment(document)

    payload['attachment'] = attachment

    response = self.post(payload=payload)
    return response['uuid']

  def link_document(self,rec_id,document_id):
    """
    Links an existing document on the ENCODE Portal to another existing object on the Portal via
    the latter's "documents" property.

    Args:
        rec_id: `str`. A DCC object identifier, i.e. accession, @id, UUID, ..., of the object to 
          link the document to.
        document_id: `str`. An identifier of a `document` record.
    """
    
    #Need to compare the documents at the primary ID level ('@id' property) in order to ensure the
    # document isn't already linked. If not comparing at this identifier type and instead some
    # other type (i.e. alias, uuid), then the document will be relinked as a duplicate.

    doc_json = self.get(ignore404=False,rec_ids=document_id)
    doc_primary_id = doc_json["@id"]

    rec_json = self.get(ignore404=False,rec_ids=rec_id)
    try:
      rec_document_primary_ids = rec_json["documents"]
    except KeyError:
      #There aren't any documents at present.
      rec_document_primary_ids = []

    if doc_primary_id in rec_document_primary_ids:
      self.debug_logger.debug("Will not attempt to link document {} to {} since it is already linked.".format(document_id,rec_id))
      return

    #Add primary ID of new document to link.
    rec_document_primary_ids.append(doc_primary_id)
    #Originally in form of [u'/documents/ba93f5cc-a470-41a2-842f-2cb3befbeb60/',
    #                       u'/documents/tg81g5aa-a580-01a2-842f-2cb5iegcea03, ...]
    #Strip off the /documents/ prefix from each document UUID:
    payload = {}
    payload[self.ENCID_KEY] = rec_id
    payload["documents"] = rec_document_primary_ids
    self.patch(payload=payload)

#When appending "?datastore=database" to the URL. As Esther stated: "_indexer to the end of the
# URL to see the status of elastic search like
# https://www.encodeproject.org/_indexer if it's indexing it will say the status is "indexing",
# versus waiting" and the results property will indicate the last object that was indexed."
