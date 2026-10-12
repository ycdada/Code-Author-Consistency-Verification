"""Synthetic engineering fixtures only; not authorship effectiveness tests."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
import structural_features as sf

BASE = 'int sum(int n){ int total=0; for(int i=0;i<n;++i){total+=i;} return total; }'


class DependencyFreeTests(unittest.TestCase):
    def test_empty_has_no_vector(self):
        result = sf.extract('  \n')
        self.assertFalse(result['accepted'])
        self.assertEqual(result['features'], {})
        self.assertEqual(result['rejection_reasons'], ['empty_input'])

    def test_invalid_utf8_and_size_are_rejected(self):
        self.assertEqual(sf.extract(b'\xff')['rejection_reasons'], ['invalid_utf8'])
        self.assertEqual(sf.extract('x' * (sf.MAX_BYTES + 1))['rejection_reasons'], ['input_too_large'])

    def test_missing_dependency_does_not_fall_back(self):
        with patch.object(sf, 'dependency_status', return_value={'available': False, 'installed': {}}):
            with self.assertRaises(sf.DependencyUnavailable):
                sf.extract(BASE)

    def test_input_types(self):
        with self.assertRaises(TypeError):
            sf.extract(None)
        with self.assertRaises(TypeError):
            sf.transform(BASE)


@unittest.skipUnless(sf.dependency_status()['available'], 'Optional pinned C++ CST dependencies unavailable')
class StructuralTests(unittest.TestCase):
    def test_formatting_comments_and_renaming_invariant(self):
        changed = '/* while fake */ int count ( int bound ) {\n int acc = 0;\n for (int idx = 0; idx < bound; ++idx) { // return\n acc += idx; } return acc; }'
        a, b = sf.extract(BASE), sf.extract(changed)
        self.assertTrue(a['accepted'])
        self.assertEqual(a['features'], b['features'])
        self.assertEqual(a['shape_summary'], b['shape_summary'])
        self.assertNotEqual(a['positions'], b['positions'])

    def test_structure_changes(self):
        result = sf.extract('int sum(int n){int total=0; if(n>0){total=n;} return total;}')
        self.assertTrue(result['accepted'])
        self.assertNotEqual(sf.extract(BASE)['features'], result['features'])
        self.assertIn('node:if_statement', result['features'])

    def test_literal_contents_are_opaque(self):
        a = 'int main(){const char* s="hello"; int n=1; return n;}'
        b = 'int main(){const char* x="for while if return \\n"; int value=928; return value;}'
        first, second = sf.extract(a), sf.extract(b)
        self.assertTrue(first['accepted'])
        self.assertEqual(first['features'], second['features'])
        self.assertNotIn('node:for_statement', second['features'])
        self.assertNotIn('node:string_content', second['features'])
        self.assertNotIn('node:escape_sequence', second['features'])

    def test_error_and_missing_are_flagged(self):
        for source in ['int main( { @@@ return ;', 'int main(){ int x=1; return x }']:
            result = sf.extract(source)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['features'], {})
            self.assertIn('parse_error_or_missing', result['rejection_reasons'])
            self.assertGreater(result['quality']['error_or_missing_ratio'], 0)
            self.assertTrue(result['diagnostics'])

    def test_short_and_comment_only_rejected(self):
        for source in ['int x;', '// while fake\n/* int main() {} */']:
            result = sf.extract(source)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['features'], {})
            self.assertIn('insufficient_structure', result['rejection_reasons'])

    def test_unicode_positions_use_bytes(self):
        source = '// 中文\n' + BASE
        result = sf.extract(source)
        function = next(x for x in result['positions'] if x['type'] == 'function_definition')
        self.assertEqual(function['start_point'], [1, 0])
        self.assertEqual(function['start_byte'], len('// 中文\n'.encode('utf-8')))
        self.assertTrue(source.encode()[function['start_byte']:function['end_byte']].startswith(b'int sum'))

    def test_transform_is_deterministic_and_not_fitted(self):
        a = sf.transform([BASE, ''])
        self.assertEqual(a, sf.transform([BASE, '']))
        self.assertTrue(a[0]['accepted'])
        self.assertFalse(a[1]['accepted'])
        self.assertEqual(a[0]['positions'], [])
        self.assertTrue(any(x.startswith('path3:') for x in a[0]['features']))

    def test_raw_strings_and_macros_not_executed(self):
        source = '#define DANGER system("touch /tmp/SHOULD_NOT_EXIST")\nint main(){const char* s=R"xx(for if { })xx"; int n=1; return n;}'
        result = sf.extract(source)
        self.assertTrue(result['accepted'])
        self.assertNotIn('node:if_statement', result['features'])
        self.assertNotIn('node:call_expression', result['features'])
        self.assertEqual(result['partition'], 'raw')


if __name__ == '__main__':
    unittest.main()
