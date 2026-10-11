"""Research-only NumPy tests. Run explicitly, outside base dependency suite."""
import unittest
from dataclasses import replace

import numpy as np
from research.active_control import (Design, fixture, convolution_matrix, render,
                                    optimize, metrics, mismatch, experiment)


class ActiveControlTests(unittest.TestCase):
    def test_fixture_reproducibility_and_sub_bandwidth(self):
        h, t = fixture()
        h2, t2 = fixture()
        np.testing.assert_array_equal(h, h2)
        np.testing.assert_array_equal(t, t2)
        self.assertEqual(h.shape, (6, 3, 384))
        self.assertFalse(np.array_equal(h[0], h[4]))
        self.assertEqual(fixture(6)[0].shape[1], 6)
        # Sub's modeled LP response is actually weaker at high frequencies.
        spectra = np.abs(np.fft.rfft(h, n=1024, axis=-1))
        self.assertLess(spectra[:, 2, 250:400].mean(), spectra[:, :2, 250:400].mean())

    def test_linear_convolution_has_complete_tail(self):
        h, _ = fixture()
        rng = np.random.default_rng(1)
        g = rng.normal(size=(3, 2, 48))
        a = convolution_matrix(h, 48)
        via_matrix = np.einsum('pns,si->pin', a, g.transpose(0, 2, 1).reshape(144, 2))
        np.testing.assert_allclose(via_matrix, render(h, g), atol=1e-12)
        self.assertEqual(via_matrix.shape[-1], 431)

    def test_joint_constraints_causality_and_training_objective(self):
        d = Design()
        h, t = fixture()
        joint, receipt = optimize(h[:4], t[:4], d)
        independent, ir = optimize(h[:4], t[:4], d, independent=True)
        self.assertEqual(joint.shape, (3, 2, d.taps))
        self.assertTrue(np.isfinite(joint).all())
        self.assertLessEqual(np.max(np.sum(np.abs(joint), axis=(1, 2))), d.speaker_l1_limit + 1e-10)
        self.assertLessEqual(np.linalg.norm(joint), d.total_l2_limit + 1e-10)
        self.assertTrue(receipt['monotone'])
        self.assertLess(receipt['objective_final'], receipt['objective_initial'])
        self.assertLessEqual(receipt['objective_final'], ir['objective_final'] + 1e-6)
        self.assertTrue(np.all(independent[2] == 0))
        self.assertTrue(np.all(independent[0, 1] == 0))

    def test_invalid_inputs_fail_closed(self):
        h, t = fixture()
        for d in (replace(Design(), ridge=0), replace(Design(), delay=48),
                  replace(Design(), total_l2_limit=float('nan'))):
            with self.assertRaises(ValueError):
                optimize(h, t, d)
        h[0, 0, 0] = float('nan')
        with self.assertRaises(ValueError):
            optimize(h, t)

    def test_late_weight_and_tight_effort_constraints_are_effective(self):
        h, t = fixture()
        d = Design()
        weighted, _ = optimize(h[:4], t[:4], d)
        almost_unweighted, _ = optimize(h[:4], t[:4], replace(d, late_weight=0.001))
        self.assertLess(metrics(h[:4], weighted, t[:4], d)['late_energy_relative_target_db'],
                        metrics(h[:4], almost_unweighted, t[:4], d)['late_energy_relative_target_db'])
        tight = replace(d, speaker_l1_limit=0.4, total_l2_limit=0.3)
        constrained, _ = optimize(h[:4], t[:4], tight)
        self.assertLessEqual(np.linalg.norm(constrained), 0.3 + 1e-10)
        self.assertLessEqual(np.sum(np.abs(constrained), axis=(1, 2)).max(), 0.4 + 1e-10)

    def test_metrics_retain_mismatch_and_regressions(self):
        result = experiment(3)
        self.assertIn('SYNTHETIC', result['label'])
        self.assertIn('mismatch_held_out', result['methods']['joint'])
        self.assertGreater(len(result['regressions']), 0)
        h, t = fixture()
        g, _ = optimize(h[:4], t[:4])
        self.assertNotEqual(metrics(h[4:], g, t[4:], Design())['target_nmse'],
                            metrics(mismatch(h[4:]), g, t[4:], Design())['target_nmse'])


if __name__ == '__main__':
    unittest.main()
