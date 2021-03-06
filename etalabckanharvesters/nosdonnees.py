#! /usr/bin/env python
# -*- coding: utf-8 -*-


# Etalab-CKAN-Harvesters -- Harvesters for Etalab's CKAN
# By: Emmanuel Raviart <emmanuel@raviart.com>
#
# Copyright (C) 2013 Etalab
# http://github.com/etalab/etalab-ckan-harvesters
#
# This file is part of Etalab-CKAN-Harvesters.
#
# Etalab-CKAN-Harvesters is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Etalab-CKAN-Harvesters is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


"""Harvest NosDonnées.fr from RegardsCitoyens.org

http://www.nosdonnees.fr/
"""


import argparse
import ConfigParser
import itertools
import json
import logging
import os
import sys
import urllib
import urllib2
import urlparse

from biryani1 import baseconv, custom_conv, states, strings
from ckantoolbox import ckanconv

from . import helpers


app_name = os.path.splitext(os.path.basename(__file__))[0]
conv = custom_conv(baseconv, ckanconv, states)
log = logging.getLogger(app_name)


def after_ckan_json_to_package(package, state = None):
    if package is None:
        return package, None
    package = package.copy()

    if package.get('extras'):
        extras = []
        for extra in package['extras']:
            value = extra.get('value')
            if value is not None:
                try:
                    value = json.loads(value)
                except ValueError:
                    # Value is not a JSON but only a string.
                    pass
            if value in (None, ''):
                continue
            # Add a new extra with only key and value.
            extras.append(dict(
                key = extra['key'],
                value = value,
                ))
        package['extras'] = extras or None

    if package.get('private', False) or package.get('capacity') == u'private':
        return None, None

    package.pop('capacity', None)
    del package['id']  # Don't reuse source ID in target.
    package.pop('revision_id', None)
    package.pop('users', None)  # Don't reuse source users in target.

    if package.get('resources'):
        resources = []
        for resource in package['resources']:
            resource = resource.copy()
            if resource.pop('capacity', None) == u'private':
                continue
            resource.pop('revision_id', None)
            resource.pop('URI', None)
            resources.append(resource)
        package['resources'] = resources
    if not package.get('resources'):
        return None, None

    return package, None


def main():
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument('config', help = 'path of configuration file')
    parser.add_argument('-d', '--dry-run', action = 'store_true',
        help = "simulate harvesting, don't update CKAN repository")
    parser.add_argument('-v', '--verbose', action = 'store_true', help = 'increase output verbosity')

    global args
    args = parser.parse_args()
    logging.basicConfig(level = logging.DEBUG if args.verbose else logging.WARNING, stream = sys.stdout)

    config_parser = ConfigParser.SafeConfigParser(dict(
        here = os.path.dirname(os.path.abspath(os.path.normpath(args.config))),
        ))
    config_parser.read(args.config)
    conf = conv.check(conv.pipe(
        conv.test_isinstance(dict),
        conv.struct(
            {
                'ckan.api_key': conv.pipe(
                    conv.cleanup_line,
                    conv.not_none,
                    ),
                'ckan.site_url': conv.pipe(
                    conv.make_input_to_url(error_if_fragment = True, error_if_path = True, error_if_query = True,
                        full = True),
                    conv.not_none,
                    ),
                'user_agent': conv.pipe(
                    conv.cleanup_line,
                    conv.not_none,
                    ),
                },
            default = 'drop',
            ),
        conv.not_none,
        ))(dict(config_parser.items('Etalab-CKAN-Harvesters')), conv.default_state)

    harvester = helpers.Harvester(
        old_supplier_title = u'Regards Citoyens',
        supplier_abbreviation = u'nd',
        supplier_title = u'NosDonnées.fr',
        target_headers = {
            'Authorization': conf['ckan.api_key'],
            'User-Agent': conf['user_agent'],
            },
        target_site_url = conf['ckan.site_url'],
        )
    source_headers = {
        'User-Agent': conf['user_agent'],
        }
    source_site_url = u'http://www.nosdonnees.fr/'

    if not args.dry_run:
        harvester.retrieve_target()

    # Retrieve names of packages in source.
    request = urllib2.Request(urlparse.urljoin(source_site_url, 'api/3/action/package_list'),
        headers = source_headers)
    response = urllib2.urlopen(request, '{}')  # CKAN 1.8 requires a POST.
    response_dict = json.loads(response.read())
    packages_source_name = conv.check(conv.pipe(
        conv.ckan_json_to_name_list,
        conv.not_none,
        ))(response_dict['result'], state = conv.default_state)

    # Retrieve packages from source.
    for package_source_name in packages_source_name:
        request = urllib2.Request(urlparse.urljoin(source_site_url, 'api/3/action/package_show'),
            headers = source_headers)
        response = urllib2.urlopen(request, urllib.quote(json.dumps(dict(
                id = package_source_name,
                ))))  # CKAN 1.8 requires a POST.
        response_dict = json.loads(response.read())
        package = conv.check(conv.pipe(
            conv.make_ckan_json_to_package(drop_none_values = True),
            conv.not_none,
            after_ckan_json_to_package,
            ))(response_dict['result'], state = conv.default_state)
        if package is None:
            continue

        package_groups = package.pop('groups', None)
        groups_name = [
            group['name']
            for group in (package_groups or [])
            ]
        if 'brocas' in groups_name:
            # Brocas is already imported from Resourcerie Datalocale.
            continue
        if 'strasbourg' in groups_name:
            groups_name.remove('strasbourg')
            package['territorial_coverage'] = u'IntercommunalityOfFrance/246700488'  # CU de Strasbourg
        tags_slug = sorted(set(
            strings.slugify(tag_name)
            for tag_name in itertools.chain(
                (
                    tag['name']
                    for tag in (package.get('tags') or [])
                    ),
                groups_name,
                )
            ))
        if u'rennes' in tags_slug:
            continue
        package['tags'] = [
            dict(
                name = tag_slug,
                )
            for tag_slug in tags_slug
            ]

        url = package.pop('url', None)
        if url is not None:
            if 'opendata71' in url:
                continue  # Opendata71 is harvested by itself.
            package.setdefault(u'resources', []).append(dict(
                format = u'HTML',
                name = u'Source',
                url = url,
                ))
        source_name = package.pop('name')
        source_url = urlparse.urljoin(source_site_url, 'dataset/{}'.format(source_name))
        package[u'url'] = source_url

        package = conv.check(conv.ckan_input_package_to_output_package)(package, state = conv.default_state)
        log.info(u'Harvested package: {}'.format(package['title']))
        if not args.dry_run:
            harvester.add_package(package, harvester.supplier, source_name, source_url)

    if not args.dry_run:
        harvester.update_target()

    return 0


if __name__ == '__main__':
    sys.exit(main())
