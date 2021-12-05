import demistomock as demisto
from CommonServerPython import *  # noqa # pylint: disable=unused-wildcard-import
from CommonServerUserPython import *  # noqa

import requests
import traceback
from typing import Dict, Any

# Disable insecure warnings
requests.packages.urllib3.disable_warnings()  # pylint: disable=no-member


''' CLIENT CLASS '''


class Client(BaseClient):

    def censys_view_host_request(self, query: str):
        url_suffix = f'v2/hosts/{query}'
        res = self._http_request('GET', url_suffix)
        return res
    
    def censys_view_cert_request(self, query: str):
        url_suffix = f'v1/view/certificates/{query}'
        res = self._http_request('GET', url_suffix)
        return res

    def censys_search_ip_request(self, query: Dict, page_size: int):
        url_suffix = 'v2/hosts/search'
        params = {
            'q': query,
            'per_page': page_size
        }

        res = self._http_request('GET', url_suffix, params=params)
        return res

    def search_pagination(self, query, page_size, cursor):
        url_suffix = 'v2/hosts/search'
        params = {
            'q': query,
            'per_page': page_size,
            'cursor': cursor
        }

        res = self._http_request('GET', url_suffix, params=params)
        return res

    def censys_search_certs_request(self, data):
        url_suffix = 'v1/search/certificates'
        res = self._http_request('POST', url_suffix, json_data=data)
        return res


''' COMMAND FUNCTIONS '''


def test_module(client: Client) -> str:
    res = client.censys_view_host_request('8.8.8.8')
    if res:
        return 'ok'


def censys_view_host_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    """
    Returns host information for the specified IP address or structured certificate data for the specified SHA-256
    """

    query = args.get('query')
    res = client.censys_view_host_request(query)
    result = res.get('result', {})
    content = {
        'Name': result.get('autonomous_system', {}).get('name'),
        'Bgp Prefix': result.get('autonomous_system', {}).get('bgp_prefix'),
        'ASN': result.get('autonomous_system', {}).get('asn'),
        'Service': [{
            'Port': service.get('port'),
            'Service Name': service.get('service_name')
        } for service in result.get('services', [])],
        'Last Updated': result.get('last_updated_at')
    }

    human_readable = tableToMarkdown(f'Information for IP {query}', content)
    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='Censys.HostView',
        outputs_key_field='ip',
        outputs=result,
        raw_response=res
    )


def censys_view_cert_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    query = args.get('query')
    res = client.censys_view_cert_request(query)
    metadata = res.get('metadata')
    content = {
        'SHA 256': res.get('fingerprint_sha256'),
        'Tags': res.get('tags'),
        'Source': metadata.get('source'),
        'Added': metadata.get('added_at'),
        'Updated': metadata.get('updated_at')
    }

    human_readable = tableToMarkdown(f'Information for certificate ', content)
    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='Censys.CertificateView',
        outputs_key_field='fingerprint_sha256',
        outputs=res
    )


def censys_search_hosts_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    query = args.get('query')
    page_size = args.get('page_size', 50)
    limit = int(args.get('limit'))
    contents = []

    res = client.censys_search_ip_request(query, page_size)
    hits = res.get('result', {}).get('hits', [])
    while len(hits) < limit:
        next_page = res.get('links', {}).get('next')
        response = client.search_pagination(query, page_size, next_page)
        response_hits = response.get('result', {}).get('hits', [])
        hits.extend(response_hits)

    for hit in hits[:limit]:
        contents.append({
            'IP': hit.get('ip'),
            'Services': hit.get('services'),
            'Location Country code': hit.get('location', {}).get('country_code'),
            'Registered Country Code': hit.get('location', {}).get('registered_country_code'),
            'ASN': hit.get('autonomous_system', {}).get('asn'),
            'Description': hit.get('autonomous_system', {}).get('description'),
            'Name': hit.get('autonomous_system', {}).get('name')
        })
    headers = ['IP', 'Name', 'Description', 'ASN', 'Location Country code', 'Registered Country Code', 'Services']
    human_readable = tableToMarkdown(f'Search results for query "{query}"', contents, headers)
    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='Censys.HostSearch',
        outputs_key_field='ip',
        outputs=hits[:limit],
        raw_response=res
    )


def censys_search_certs_command(client: Client, args: Dict[str, Any]):
    limit = int(args.get('limit', 50))
    query = args.get('query')
    fields = ["parsed.fingerprint_sha256", "parsed.subject_dn", "parsed.issuer_dn", "parsed.issuer.organization",
              "parsed.validity.start", "parsed.validity.end", "parsed.names"]
    search_fields = argToList(args.get('fields'))
    if search_fields:
        fields.append(search_fields)
    contents = []
    data = {
        'query': query,
        'page': int(args.get('page', 1)),
        'fields': fields,
        'flatten': False
    }

    res = client.censys_search_certs_request(data)
    results = res.get('results')[:limit]
    for result in results:
        contents.append({
            'SHA256': result.get('parsed').get('fingerprint_sha256'),
            'Issuer dn': result.get('parsed').get('issuer_dn'),
            'Subject dn': result.get('parsed').get('subject_dn'),
            'Names': result.get('parsed').get('names'),
            'Validity': result.get('parsed').get('validity'),
            'Issuer': result.get('parsed').get('issuer'),
        })

    human_readable = tableToMarkdown(f'Search results for query "{query}"', contents)
    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='Censys.CertificateSearch',
        outputs_key_field='fingerprint_sha256',
        outputs=results,
        raw_response=res
    )


''' MAIN FUNCTION '''


def main() -> None:
    params = demisto.params()
    username = params.get('credentials', {}).get('identifier')
    password = params.get('credentials', {}).get('password')
    verify_certificate = not demisto.params().get('insecure', False)
    proxy = demisto.params().get('proxy', False)

    base_url = 'https://search.censys.io/api/'

    demisto.debug(f'Command being called is {demisto.command()}')
    try:
        headers: Dict = {'Content-Type': 'application/json'}

        client = Client(
            base_url=base_url,
            auth=(username, password),
            verify=verify_certificate,
            headers=headers,
            proxy=proxy)

        if demisto.command() == 'test-module':
            # This is the call made when pressing the integration Test button.
            result = test_module(client)
            return_results(result)

        elif demisto.command() == 'censys-host-view':
            return_results(censys_view_host_command(client, demisto.args()))
        elif demisto.command() == 'censys-certificate-view':
            return_results(censys_view_cert_command(client, demisto.args()))
        elif demisto.command() == 'censys-hosts-search':
            return_results(censys_search_hosts_command(client, demisto.args()))
        elif demisto.command() == 'censys-certificates-search':
            return_results(censys_search_certs_command(client, demisto.args()))

    # Log exceptions and return errors
    except Exception as e:
        demisto.error(traceback.format_exc())  # print the traceback
        return_error(f'Failed to execute {demisto.command()} command.\nError:\n{str(e)}')


''' ENTRY POINT '''


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()