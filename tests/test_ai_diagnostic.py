"""Portable tests with original synthetic text, never benchmark evidence."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

MODULE = Path(__file__).resolve().parents[1]/'experiments'/'ai_diagnostic.py'
spec = importlib.util.spec_from_file_location('ai_diagnostic', MODULE)
a = importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)

class DiagnosticTests(unittest.TestCase):
    def make_rows(self):
        return [a.record(f'{s}:{i}',f'int value_{s}_{i} = {i};',s,int(s!='human'))
                for s in ['human','generator_a','generator_b'] for i in range(30)]

    def test_token_hash_ignores_comments_not_strings(self):
        self.assertEqual(a.token_hash('int x=1; // hi'),a.token_hash('/*x*/ int  x = 1 ;'))
        self.assertNotEqual(a.token_hash('puts("a b");'),a.token_hash('puts("ab");'))
        self.assertNotEqual(a.token_hash('int x=1;'),a.token_hash('int y=1;'))
        self.assertNotEqual(a.token_hash('puts("//keep");'),a.token_hash('puts("");'))

    def test_quarantine_removes_all_clone_members(self):
        rows=[a.record('a','int x=1;','human',0),a.record('b','int x = 1; //x','g',1),a.record('c','int y=2;','g',1)]
        kept,removed=a.quarantine(rows)
        self.assertEqual([r.key for r in kept],['c']);self.assertEqual(len(removed),2)

    def test_deterministic_split_and_heldout(self):
        rows=self.make_rows();one=a.split_fold(rows,'generator_a');two=a.split_fold(list(reversed(rows)),'generator_a')
        self.assertEqual(one,two)
        self.assertFalse(any(r.source=='generator_a' for r in one['train']+one['dev']))
        self.assertEqual(sum(r.source=='generator_a' for r in one['test']),30)
        self.assertEqual([sum(r.source=='human' for r in one[k]) for k in ['train','dev','test']],[18,6,6])

    def test_threshold_honors_fpr_ties(self):
        y=[0,0,1,1];p=[.8,.1,.8,.9]
        threshold=a.choose_threshold(y,p,0.)
        self.assertEqual(threshold,.9)
        self.assertEqual(a.evaluate(y,p,threshold)['fp'],0)

    def test_threshold_can_reject_everything(self):
        th=a.choose_threshold([0,1],[.9,.8],0.)
        self.assertGreater(th,.9)

    def test_metrics_abstention_and_wilson(self):
        m=a.evaluate([0,0,1,1],[.1,.6,.4,.9],.5,.2)
        self.assertEqual(m['fpr'],.5);self.assertEqual(m['tpr'],.5)
        self.assertEqual(m['coverage'],.5);self.assertEqual(m['selective_fpr'],0)
        self.assertEqual(m['selective_tpr'],1)
        self.assertLess(m['fpr_naive_wilson95'][0],.5)
        self.assertIsNone(a.wilson(0,0))

    def test_lexical_vector_finite_fixed_size(self):
        self.assertEqual(len(a.lexical('int main() {}')),len(a.FEATURES))
        self.assertTrue(np.isfinite(a.lexical('')).all())

    def test_train_only_vectorizer(self):
        model=a.build_model('char_tfidf')
        model.fit(['aaa aaa one','aaa aaa two','bbb bbb one','bbb bbb two'],[0,0,1,1])
        before=dict(model[0].vocabulary_)
        model.predict_proba(['zzzzzz unseen_token'])
        self.assertEqual(before,model[0].vocabulary_);self.assertNotIn('zzz',before)

    def test_output_refuses_repository(self):
        with self.assertRaises(ValueError): a.run('/missing',MODULE.parent/'forbidden_output')

    def test_missing_labels_refused(self):
        with self.assertRaises(ValueError): a.choose_threshold([0,0],[.1,.2])
        with self.assertRaises(ValueError): a.record('x','','human',0)
        with self.assertRaises(ValueError): a.split_fold([a.record('x','int x;','human',0)],'g')

if __name__=='__main__':unittest.main()

class QualityTests(unittest.TestCase):
    def test_single_fence_extracts_without_label(self):
        text,flags=a.quality('Here is code\n```cpp\nint main(){return 0;}\n```\nExplanation')
        self.assertEqual(text,'int main(){return 0;}')
        self.assertIn('single_code_fence',flags)
    def test_multi_and_unclosed_fences_refused(self):
        self.assertIsNone(a.quality('```cpp\nx\n```\n```cpp\ny\n```')[0])
        self.assertIsNone(a.quality('```cpp\nx')[0])
    def test_unfenced_unchanged(self):
        text='int main(){return 0;}'
        self.assertEqual(a.quality(text)[0],text)
    def test_lexical_quality_not_parser(self):
        self.assertIn('unclosed_lexical_heuristic',a.quality('int x; /*')[1])
        self.assertNotIn('unclosed_lexical_heuristic',a.quality('puts("/*");')[1])
