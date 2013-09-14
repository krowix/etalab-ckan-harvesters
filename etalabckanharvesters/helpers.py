#! /usr/bin/env python
# -*- coding: utf-8 -*-


# Etalab-CKAN-Harvesters -- Harvesters for Etalab's CKAN
# By: Emmanuel Raviart <emmanuel@raviart.com>
#
# Copyright (C) 2013 Emmanuel Raviart
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


"""Helpers for harvesters"""


import cStringIO
import csv
import itertools
import json
import logging
import urllib
import urllib2
import urlparse

from biryani1 import baseconv, custom_conv, states, strings
from ckantoolbox import ckanconv, filestores

conv = custom_conv(baseconv, ckanconv, states)
log = logging.getLogger(__name__)


class Harvester(object):
    existing_packages_name = None
    old_supplier_name = None
    old_supplier_title = None
    organization_by_name = None
    organization_name_by_package_name = None
    package_by_name = None
    package_source_by_name = None
    packages_by_organization_name = None
    supplier_abbreviation = None
    supplier = None
    supplier_name = None
    supplier_title = None
    target_headers = None
    target_site_url = None

    def __init__(self, old_supplier_title = None, supplier_abbreviation = None, supplier_title = None,
            target_headers = None, target_site_url = None):
        if old_supplier_title is not None:
            assert isinstance(old_supplier_title, unicode)
            self.old_supplier_title = old_supplier_title
            old_supplier_name = strings.slugify(old_supplier_title)
            assert old_supplier_name
            assert len(old_supplier_name) <= 100
            self.old_supplier_name = old_supplier_name

        assert isinstance(supplier_abbreviation, unicode)
        assert supplier_abbreviation == strings.slugify(supplier_abbreviation)
        assert 1 < len(supplier_abbreviation) < 5
        self.supplier_abbreviation = supplier_abbreviation

        assert isinstance(supplier_title, unicode)
        self.supplier_title = supplier_title
        supplier_name = strings.slugify(supplier_title)
        assert supplier_name
        assert len(supplier_name) <= 100
        self.supplier_name = supplier_name

        assert isinstance(target_headers, dict)
        assert isinstance(target_headers['Authorization'], basestring)
        assert isinstance(target_headers['User-Agent'], basestring)
        self.target_headers = target_headers

        assert isinstance(target_site_url, unicode)
        self.target_site_url = target_site_url

        self.existing_packages_name = set()
        self.organization_by_name = {}
        self.organization_name_by_package_name = {}
        self.package_by_name = {}
        self.package_source_by_name = {}
        self.packages_by_organization_name = {}

    def add_package(self, package, organization, source_name, source_url):
        name = self.name_package(package['title'])
        if package.get('name') is None:
            package['name'] = name
        else:
            assert package['name'] == name, package

        package['owner_org'] = organization['id']
        package['supplier_id'] = self.supplier['id']

        assert name not in self.package_by_name
        self.package_by_name[name] = package

        assert name not in self.organization_name_by_package_name
        self.organization_name_by_package_name[name] = organization['name']

        assert name not in self.package_source_by_name
        self.package_source_by_name[name] = dict(
            name = source_name,
            url = source_url,
            )

    def name_package(self, title):
        for index in itertools.count(1):
            differentiator = u'-{}'.format(index) if index > 1 else u''
            name = u'{}{}-{}'.format(
                strings.slugify(title)[:100 - len(self.supplier_abbreviation) - 1 - len(differentiator)],
                differentiator,
                self.supplier_abbreviation,
                )
            if name not in self.package_by_name:
                return name

    def retrieve_supplier_existing_packages(self, supplier):
        for package in (supplier.get('packages') or []):
            if not package['name'].startswith('jeux-de-donnees-'):
                continue
            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/package_show?id={}'.format(package['name'])), headers = self.target_headers)
            response = urllib2.urlopen(request)
            response_dict = json.loads(response.read())
            package = conv.check(
                conv.make_ckan_json_to_package(drop_none_values = True),
                )(response_dict['result'], state = conv.default_state)
            if package is None:
                continue
            for tag in (package.get('tags') or []):
                if tag['name'] == 'liste-de-jeux-de-donnees':
                    break
            else:
                # This dataset doesn't contain a list of datasets. Ignore it.
                continue
            self.existing_packages_name.add(package['name'])
            for resource in (package.get('resources') or []):
                response = urllib2.urlopen(resource['url'])
                packages_csv_reader = csv.reader(response, delimiter = ';', quotechar = '"')
                packages_csv_reader.next()
                for row in packages_csv_reader:
                    package_infos = dict(
                        (key, value.decode('utf-8'))
                        for key, value in zip(['title', 'name', 'source_name'], row)
                        )
                    self.existing_packages_name.add(package_infos['name'])

    def retrieve_target(self):
        # Retrieve supplying organization (that will contain all harvested datasets).
        request = urllib2.Request(urlparse.urljoin(self.target_site_url,
            'api/3/action/organization_show?id={}'.format(self.supplier_name)), headers = self.target_headers)
        response = urllib2.urlopen(request)
        response_dict = json.loads(response.read())
        supplier = conv.check(conv.pipe(
            conv.make_ckan_json_to_organization(drop_none_values = True),
            conv.not_none,
            ))(response_dict['result'], state = conv.default_state)
        self.organization_by_name[self.supplier_name] = self.supplier = supplier
        self.retrieve_supplier_existing_packages(supplier)

        if self.old_supplier_name is not None:
            # Retrieve old supplying organization.
            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/organization_show?id={}'.format(self.old_supplier_name)), headers = self.target_headers)
            response = urllib2.urlopen(request)
            response_dict = json.loads(response.read())
            old_supplier = conv.check(conv.pipe(
                conv.make_ckan_json_to_organization(drop_none_values = True),
                conv.not_none,
                ))(response_dict['result'], state = conv.default_state)
            self.organization_by_name[self.old_supplier_name] = old_supplier
            self.retrieve_supplier_existing_packages(old_supplier)

    def update_target(self):
        # Upsert packages to target.
        for package_name, package in self.package_by_name.iteritems():
            if package_name in self.existing_packages_name:
                log.info(u'Updating package: {}'.format(package['title']))
                self.existing_packages_name.remove(package_name)
                request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                    'api/3/action/package_update?id={}'.format(package_name)), headers = self.target_headers)
                try:
                    response = urllib2.urlopen(request, urllib.quote(json.dumps(package)))
                except urllib2.HTTPError as response:
                    response_text = response.read()
                    try:
                        response_dict = json.loads(response_text)
                    except ValueError:
                        log.error(u'An exception occured while updating package: {}'.format(package))
                        log.error(response_text)
                        continue
                    log.error(u'An error occured while updating package: {}'.format(package))
                    for key, value in response_dict.iteritems():
                        print '{} = {}'.format(key, value)
                else:
                    assert response.code == 200
                    response_dict = json.loads(response.read())
                    assert response_dict['success'] is True
#                    updated_package = response_dict['result']
#                    pprint.pprint(updated_package)
            else:
                log.info(u'Creating package: {}'.format(package['title']))
                request = urllib2.Request(urlparse.urljoin(self.target_site_url, 'api/3/action/package_create'),
                    headers = self.target_headers)
                try:
                    response = urllib2.urlopen(request, urllib.quote(json.dumps(package)))
                except urllib2.HTTPError as response:
                    response_text = response.read()
                    try:
                        response_dict = json.loads(response_text)
                    except ValueError:
                        log.error(u'An exception occured while creating package: {}'.format(package))
                        log.error(response_text)
                        continue
                    error = response_dict.get('error', {})
                    if error.get('__type') == u'Validation Error' and error.get('name'):
                        # A package with the same name already exists. Maybe it is deleted. Undelete it.
                        package['state'] = 'active'
                        request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                            'api/3/action/package_update?id={}'.format(package_name)), headers = self.target_headers)
                        try:
                            response = urllib2.urlopen(request, urllib.quote(json.dumps(package)))
                        except urllib2.HTTPError as response:
                            response_text = response.read()
                            try:
                                response_dict = json.loads(response_text)
                            except ValueError:
                                log.error(u'An exception occured while undeleting package: {}'.format(package))
                                log.error(response_text)
                                continue
                            log.error(u'An error occured while undeleting package: {}'.format(package))
                            for key, value in response_dict.iteritems():
                                print '{} = {}'.format(key, value)
                        else:
                            assert response.code == 200
                            response_dict = json.loads(response.read())
                            assert response_dict['success'] is True
#                            updated_package = response_dict['result']
#                            pprint.pprint(updated_package)
                    else:
                        log.error(u'An error occured while creating package: {}'.format(package))
                        for key, value in response_dict.iteritems():
                            print '{} = {}'.format(key, value)
                else:
                    assert response.code == 200
                    response_dict = json.loads(response.read())
                    assert response_dict['success'] is True
#                    created_package = response_dict['result']
#                    pprint.pprint(created_package)

            # Read updated package.
            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/package_show?id={}'.format(package_name)), headers = self.target_headers)
            response = urllib2.urlopen(request)
            response_dict = json.loads(response.read())
            package = conv.check(conv.pipe(
                conv.make_ckan_json_to_package(drop_none_values = True),
                conv.not_none,
                ))(response_dict['result'], state = conv.default_state)
            self.packages_by_organization_name.setdefault(self.organization_name_by_package_name[package_name],
                []).append(package)

        # Upsert lists of harvested packages into target.
        for organization_name, organization in self.organization_by_name.iteritems():
            package_title = u'Jeux de données - {}'.format(organization['title'])
            package_name = self.name_package(package_title)
            self.existing_packages_name.discard(package_name)
            packages = self.packages_by_organization_name.get(organization_name)
            if packages:
                log.info(u'Upserting package: {}'.format(package_name))
                packages_file = cStringIO.StringIO()
                packages_csv_writer = csv.writer(packages_file, delimiter = ';', quotechar = '"',
                    quoting = csv.QUOTE_MINIMAL)
                packages_csv_writer.writerow([
                    'Titre',
                    'Nom',
                    'Nom original',
                    'URL originale'
                    ])
                for package in packages:
                    package_source = self.package_source_by_name[package['name']]
                    packages_csv_writer.writerow([
                        package['title'].encode('utf-8'),
                        package['name'].encode('utf-8'),
                        package_source['name'].encode('utf-8'),
                        package_source['url'].encode('utf-8'),
                        ])
                file_metadata = filestores.upload_file(self.target_site_url, package_name,
                    packages_file.getvalue(), self.target_headers)

                package = dict(
                    author = self.supplier['title'],
                    license_id = 'fr-lo',
                    name = package_name,
                    notes = u'''Les jeux de données fournis par {} pour data.gouv.fr.'''.format(organization['title']),
                    owner_org = self.supplier['id'],
                    resources = [
                        dict(
                            created = file_metadata['_creation_date'],
                            format = 'CSV',
                            hash = file_metadata['_checksum'],
                            last_modified = file_metadata['_last_modified'],
                            name = package_name + u'.txt',
                            size = file_metadata['_content_length'],
                            url = file_metadata['_location'],
#                            revision_id – (optional)
#                            description (string) – (optional)
#                            resource_type (string) – (optional)
#                            mimetype (string) – (optional)
#                            mimetype_inner (string) – (optional)
#                            webstore_url (string) – (optional)
#                            cache_url (string) – (optional)
#                            cache_last_updated (iso date string) – (optional)
#                            webstore_last_updated (iso date string) – (optional)
                            ),
                        ],
                    tags = [
                        dict(
                            name = 'liste-de-jeux-de-donnees',
                            ),
                        ],
                    title = package_title,
                    )
                self.upsert_package(package)
            else:
                # Delete dataset if it exists.
                log.info(u'Deleting package: {}'.format(package_name))

                # Retrieve package id (needed for delete).
                request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                    'api/3/action/package_show?id={}'.format(package_name)), headers = self.target_headers)
                try:
                    response = urllib2.urlopen(request)
                except urllib2.HTTPError as response:
                    if response.code != 404:
                        raise
                    # Package already deleted. Do nothing.
                    log.warning(u"Package to delete doesn't exist: {}".format(package_name))
                else:
                    response_dict = json.loads(response.read())
                    existing_package = response_dict['result']

                    # TODO: To replace with package_purge when it is available.
                    request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                        'api/3/action/package_delete?id={}'.format(package_name)), headers = self.target_headers)
                    response = urllib2.urlopen(request, urllib.quote(json.dumps(existing_package)))
                    response_dict = json.loads(response.read())
#                    deleted_package = response_dict['result']
#                    pprint.pprint(deleted_package)

        # Delete obsolete packages.
        for package_name in self.existing_packages_name:
            # Retrieve package id (needed for delete).
            log.info(u'Deleting package: {}'.format(package_name))
            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/package_show?id={}'.format(package_name)), headers = self.target_headers)
            try:
                response = urllib2.urlopen(request)
            except urllib2.HTTPError as response:
                if response.code != 404:
                    raise
                # Package already deleted. Do nothing.
            else:
                response_dict = json.loads(response.read())
                existing_package = response_dict['result']

                request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                    'api/3/action/package_delete?id={}'.format(package_name)), headers = self.target_headers)
                response = urllib2.urlopen(request, urllib.quote(json.dumps(existing_package)))
                response_dict = json.loads(response.read())
#                deleted_package = response_dict['result']
#                pprint.pprint(deleted_package)

    def upsert_organization(self, organization):
        name = strings.slugify(organization['title'])[:100]

        existing_organization = self.organization_by_name.get(name)
        if existing_organization is not None:
            return existing_organization

        log.info(u'Upserting organization: {}'.format(organization['title']))
        if organization.get('name') is None:
            organization['name'] = name
        else:
            assert organization['name'] == name, organization

        request = urllib2.Request(urlparse.urljoin(self.target_site_url,
            'api/3/action/organization_show?id={}'.format(name)), headers = self.target_headers)
        try:
            response = urllib2.urlopen(request)
        except urllib2.HTTPError as response:
            if response.code != 404:
                raise
            existing_organization = {}
        else:
            response_text = response.read()
            try:
                response_dict = json.loads(response_text)
            except ValueError:
                log.error(u'An exception occured while reading organization: {0}'.format(name))
                log.error(response_text)
                raise
            existing_organization = conv.check(conv.pipe(
                conv.make_ckan_json_to_organization(drop_none_values = True),
                conv.not_none,
                ))(response_dict['result'], state = conv.default_state)

            organization_infos = organization
            organization = conv.check(conv.ckan_input_organization_to_output_organization)(existing_organization,
                state = conv.default_state)
            organization.update(
                (key, value)
                for key, value in organization_infos.iteritems()
                if value is not None
                )

        if existing_organization.get('id') is None:
            # Create organization.
            request = urllib2.Request(urlparse.urljoin(self.target_site_url, 'api/3/action/organization_create'),
                headers = self.target_headers)
            try:
                response = urllib2.urlopen(request, urllib.quote(json.dumps(organization)))
            except urllib2.HTTPError as response:
                response_text = response.read()
                log.error(u'An exception occured while creating organization: {0}'.format(organization))
                try:
                    response_dict = json.loads(response_text)
                except ValueError:
                    log.error(response_text)
                    raise
                for key, value in response_dict.iteritems():
                    log.debug('{} = {}'.format(key, value))
                raise
            else:
                assert response.code == 200
                response_dict = json.loads(response.read())
                assert response_dict['success'] is True
                created_organization = response_dict['result']
#                pprint.pprint(created_organization)
                organization['id'] = created_organization['id']
        else:
            # Update organization.
            organization['id'] = existing_organization['id']
            organization['state'] = 'active'

            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/organization_update?id={}'.format(name)), headers = self.target_headers)
            try:
                response = urllib2.urlopen(request, urllib.quote(json.dumps(organization)))
            except urllib2.HTTPError as response:
                response_text = response.read()
                log.error(u'An exception occured while updating organization: {0}'.format(organization))
                try:
                    response_dict = json.loads(response_text)
                except ValueError:
                    log.error(response_text)
                    raise
                for key, value in response_dict.iteritems():
                    log.debug('{} = {}'.format(key, value))
                raise
            else:
                assert response.code == 200
                response_dict = json.loads(response.read())
                assert response_dict['success'] is True
#                updated_organization = response_dict['result']
#                pprint.pprint(updated_organization)

        self.organization_by_name[name] = organization
        return organization

    def upsert_package(self, package):
        name = self.name_package(package['title'])
        if package.get('name') is None:
            package['name'] = name
        else:
            assert package['name'] == name, package

        request = urllib2.Request(urlparse.urljoin(self.target_site_url,
            'api/3/action/package_show?id={}'.format(name)), headers = self.target_headers)
        try:
            response = urllib2.urlopen(request)
        except urllib2.HTTPError as response:
            if response.code != 404:
                raise
            existing_package = {}
        else:
            response_text = response.read()
            try:
                response_dict = json.loads(response_text)
            except ValueError:
                log.error(u'An exception occured while reading package: {0}'.format(package))
                log.error(response_text)
                raise
            existing_package = conv.check(conv.pipe(
                conv.make_ckan_json_to_package(drop_none_values = True),
                conv.not_none,
                ))(response_dict['result'], state = conv.default_state)
        if existing_package.get('id') is None:
            # Create package.
            request = urllib2.Request(urlparse.urljoin(self.target_site_url, 'api/3/action/package_create'),
                headers = self.target_headers)
            try:
                response = urllib2.urlopen(request, urllib.quote(json.dumps(package)))
            except urllib2.HTTPError as response:
                response_text = response.read()
                log.error(u'An exception occured while creating package: {0}'.format(package))
                try:
                    response_dict = json.loads(response_text)
                except ValueError:
                    log.error(response_text)
                    raise
                for key, value in response_dict.iteritems():
                    log.debug('{} = {}'.format(key, value))
                raise
            else:
                assert response.code == 200
                response_dict = json.loads(response.read())
                assert response_dict['success'] is True
                created_package = response_dict['result']
#                pprint.pprint(created_package)
                package['id'] = created_package['id']
        else:
            # Update package.
            package['id'] = existing_package['id']
            package['state'] = 'active'

            request = urllib2.Request(urlparse.urljoin(self.target_site_url,
                'api/3/action/package_update?id={}'.format(name)), headers = self.target_headers)
            try:
                response = urllib2.urlopen(request, urllib.quote(json.dumps(package)))
            except urllib2.HTTPError as response:
                response_text = response.read()
                log.error(u'An exception occured while updating package: {0}'.format(package))
                try:
                    response_dict = json.loads(response_text)
                except ValueError:
                    log.error(response_text)
                    raise
                for key, value in response_dict.iteritems():
                    log.debug('{} = {}'.format(key, value))
                raise
            else:
                assert response.code == 200
                response_dict = json.loads(response.read())
                assert response_dict['success'] is True
#                updated_package = response_dict['result']
#                pprint.pprint(updated_package)
        return package


def get_extra(instance, key, default = UnboundLocalError):
    for extra in (instance.get('extras') or []):
        if extra['key'] == key:
            return extra.get('value')
    if default is UnboundLocalError:
        raise KeyError(key)
    return default


def pop_extra(instance, key, default = UnboundLocalError):
    for index, extra in enumerate(instance.get('extras') or []):
        if extra['key'] == key:
            del instance['extras'][index]
            return extra.get('value')
    if default is UnboundLocalError:
        raise KeyError(key)
    return default


def set_extra(instance, key, value):
    if value is None:
        pop_extra(instance, key, default = None)
        return
    if instance.get('extras') is None:
        instance['extras'] = []
    for extra in instance['extras']:
        if extra['key'] == key:
            extra['value'] = value
            return
    instance['extras'].append(dict(
        key = key,
        value = value,
        ))
