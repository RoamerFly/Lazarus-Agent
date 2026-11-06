import unittest
from pathlib import Path
from unittest.mock import patch

from diagnose import _manifest_declarations, manifest_changes


XMLTREE = """+N: a=http://schemas.android.com/apk/res/android
  E: manifest (line=2)
    E: uses-permission-sdk-23 (line=3)
      A: a:name(0x01010003)="example.PERMISSION"
      A: a:maxSdkVersion(0x01010271)=(type 0x10)0x20
    E: queries (line=4)
      E: intent (line=5)
        E: action (line=6)
          A: a:name(0x01010003)="example.QUERY"
    E: application (line=7)
      E: activity (line=8)
        A: a:name(0x01010003)=".Main" (Raw: ".Main")
        A: a:exported(0x01010010)=(type 0x12)0xffffffff
        E: intent-filter (line=9)
          E: action (line=10)
            A: a:name(0x01010003)="android.intent.action.MAIN"
        E: intent-filter (line=11)
          E: action (line=12)
            A: a:name(0x01010003)="example.OPEN"
          E: category (line=13)
            A: a:name(0x01010003)="android.intent.category.DEFAULT"
          E: data (line=14)
            A: a:mimeType(0x01010026)="text/plain"
            A: a:scheme(0x01010027)="content"
      E: provider (line=15)
        A: a:name(0x01010003)=".Private"
        A: a:exported(0x01010010)=(type 0x12)0x0
        A: a:grantUriPermissions(0x0101001b)=(type 0x12)0x0
        E: grant-uri-permission (line=16)
          A: a:path(0x0101002a)="/public"
        E: path-permission (line=17)
          A: a:pathPrefix(0x0101002b)="/private"
          A: a:readPermission(0x01010007)="example.PERMISSION"
      E: service (line=18)
        A: a:name(0x01010003)=".Service"
        A: app:exported(0x7f010001)="unrelated"
      E: uses-library (line=19)
        A: a:name(0x01010003)="example.legacy"
        A: a:required(0x0101028e)=(type 0x12)0x0
"""


class ManifestSurfaceTests(unittest.TestCase):
    def test_compiled_filters_and_uri_rules_stay_attached_to_their_components(self):
        declarations = _manifest_declarations(XMLTREE)
        activity, provider, service = declarations["components"]
        self.assertEqual(activity["name"], ".Main")
        self.assertEqual(activity["exported"], "(type 0x12)0xffffffff")
        launcher, open_record = activity["children"]
        self.assertEqual(launcher["children"], [{"type": "action", "name": "android.intent.action.MAIN"}])
        self.assertEqual(open_record["children"], [
            {"type": "action", "name": "example.OPEN"},
            {"type": "category", "name": "android.intent.category.DEFAULT"},
            {"type": "data", "mimeType": "text/plain", "scheme": "content"},
        ])
        self.assertEqual(provider["grantUriPermissions"], "(type 0x12)0x0")
        self.assertEqual(provider["children"], [
            {"type": "grant-uri-permission", "path": "/public"},
            {"type": "path-permission", "pathPrefix": "/private", "readPermission": "example.PERMISSION"},
        ])
        self.assertEqual(service, {"type": "service", "name": ".Service"})
        self.assertEqual(declarations["permissions"], [{
            "type": "uses-permission-sdk-23", "name": "example.PERMISSION", "maxSdkVersion": "(type 0x10)0x20",
        }])
        self.assertEqual(declarations["libraries"], [{
            "type": "uses-library", "name": "example.legacy", "required": "(type 0x12)0x0",
        }])

    def test_manifest_diff_detects_changes_to_child_declarations(self):
        original = _manifest_declarations(XMLTREE)
        updated = _manifest_declarations(XMLTREE.replace("example.OPEN", "example.OPEN_V2"))
        with patch("diagnose.manifest_surface", side_effect=[original, updated]):
            changes = manifest_changes(Path("original.apk"), Path("candidate.apk"), Path("tools"))
        self.assertEqual(changes["permissions"], {"added": [], "removed": []})
        self.assertEqual(changes["components"], {
            "removed": [original["components"][0]], "added": [updated["components"][0]],
        })
