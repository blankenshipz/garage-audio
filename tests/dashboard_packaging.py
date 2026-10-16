"""Small local packaging checks; not deployment or audio acceptance."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('dashboard_stage', str(ROOT/'scripts/dashboard-stage'))
spec = importlib.util.spec_from_loader(loader.name, loader)
stage = importlib.util.module_from_spec(spec)
loader.exec_module(stage)


class PackagingTests(unittest.TestCase):
    def test_enrollment_overlay_private_directory_binds_only(self):
        text=(ROOT/'compose.dashboard-enrollment.yaml').read_text()
        self.assertIn('MA_TOKEN_FILE: /auth-ma/token',text)
        self.assertIn('target: /auth-ma',text)
        self.assertIn('target: /run/garage-ma-enrollment',text)
        self.assertEqual(text.count('create_host_path: false'),2)
        self.assertEqual(text.count('read_only: true'),2)
        config='\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        for unsafe in ('docker.sock','privileged:','/dev/snd','/observer','legacy-native'):
            self.assertNotIn(unsafe,config)

    def test_enrollment_bootstrap_includes_private_dependency_and_full_source_map(self):
        text=(ROOT/'scripts/dashboard-cached-context').read_text()
        self.assertIn("x['path'].startswith(('garage_audio/','web/'))",text)
        self.assertIn('private_records.py',text)
        self.assertIn('ma-enrollment-source-manifest.json',text)
        helper=(ROOT/'scripts/install-ma-enrollment.py').read_text()
        self.assertIn('private_records.py',helper)
        self.assertIn('os.O_EXCL|os.O_NOFOLLOW',helper)
        self.assertNotIn('subprocess',helper)
        self.assertNotIn('systemctl',helper)

    def test_enrollment_required_assets_do_not_accept_stale_dashboard_pin(self):
        paths=stage.package_paths(SimpleNamespace(docker_stack=True))
        names={str(p.relative_to(ROOT)) for p in paths}
        for name in ('compose.dashboard-enrollment.yaml','docs/ma-enrollment-deploy.md',
                     'scripts/install-ma-enrollment.py','config/system/garage-ma-enrollment.service'):
            self.assertIn(name,names)
        manifest=json.loads((ROOT/stage.RELEASE_INPUT_MANIFEST).read_text())
        if manifest['status']=='pending_app_enrollment_candidate':
            self.assertIsNone(manifest['deployment_images']['dashboard'])
            self.assertFalse(stage.release_inputs(paths)['ready_for_gate'])

    def test_private_env_inputs_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'config').mkdir()
            for name in ('.env','deployment.env','.env.private','public.json'):
                (root/'config'/name).write_text('fixture\n')
            with patch.object(stage,'ROOT',root),patch.object(stage,'INPUTS',('config',)):
                names={p.name for p in stage.package_paths(SimpleNamespace(docker_stack=False))}
                self.assertEqual(names,{'public.json'})

    def test_cached_deployment_recipe_is_explicit_not_fresh_install(self):
        manifest=json.loads((ROOT/'config/docker-release-inputs.json').read_text())
        text=(ROOT/manifest['deployment_derivative_recipe']).read_text()
        self.assertIn('ARG BASE_IMAGE='+manifest['deployment_derivative_bases']['dashboard'], text)
        self.assertIn('NOT a fresh-install base', text)
        self.assertIn('COPY --chown=0:0 garage_audio /app/garage_audio', text)
        self.assertIn('COPY --chown=0:0 web /app/web', text)
        self.assertNotIn('RUN ', text)
        self.assertNotIn('--chmod=0644 garage_audio', text)
        self.assertIn('USER 1000:1000', text)
        names={str(p.relative_to(ROOT)) for p in stage.package_paths(SimpleNamespace(docker_stack=False))}
        self.assertIn(manifest['deployment_derivative_recipe'], names)
        self.assertIn('scripts/dashboard-cached-context', names)

    def test_dashboard_shutdown_keeps_init_and_full_handler_grace(self):
        text = (ROOT/'compose.dashboard.yaml').read_text()
        self.assertIn('    init: true\n', text)
        self.assertIn('    stop_grace_period: 30s', text)

    def test_dashboard_base_matches_verified_amd64_provenance(self):
        inputs = json.loads((ROOT/'config/docker-release-inputs.json').read_text())
        provenance = json.loads((ROOT/inputs['dashboard_base_provenance']).read_text())
        reference = inputs['official_bases']['dashboard']
        self.assertEqual(reference, provenance['immutable_reference'])
        self.assertEqual(provenance['platform'], 'linux/amd64')
        self.assertEqual(provenance['python_version'], '3.13.16')
        self.assertEqual(provenance['base_name'], 'debian:bookworm-slim')
        self.assertEqual(reference.split('@')[1], provenance['manifest_digest'])
        self.assertEqual(sum(layer['compressed_bytes'] for layer in provenance['layers']),
                         provenance['compressed_layer_bytes'])
        dockerfile = (ROOT/'Dockerfile.dashboard').read_text()
        self.assertIn('ARG BASE_IMAGE='+reference, dockerfile)
        self.assertIn('test "$(dpkg --print-architecture)" = amd64', dockerfile)
        names = {str(p.relative_to(ROOT)) for p in
                 stage.package_paths(SimpleNamespace(docker_stack=True))}
        self.assertIn(inputs['dashboard_base_provenance'], names)

    def test_dashboard_has_public_ipc_not_native_bridge(self):
        text = (ROOT/'compose.dashboard.yaml').read_text()
        self.assertIn('GARAGE_DEPLOYMENT_MODE: docker', text)
        self.assertIn('GARAGE_AUDIO_CONTROL_SOCKET: /run/garage-audio/public/control.sock', text)
        self.assertIn('audio-public:/run/garage-audio/public:ro', text)
        self.assertNotIn('/run/garage-speakers', text)
        self.assertNotIn('extra_hosts:', text)
        self.assertIn('networks: [music, audio-backend]', text)
        for private in ('audio-events:', 'audio-pipewire:', '/dev/snd', 'docker.sock'):
            self.assertNotIn(private, text)

    def test_real_dashboard_inputs(self):
        paths = stage.package_paths(SimpleNamespace(docker_stack=False))
        names = {str(p.relative_to(ROOT)) for p in paths}
        self.assertEqual(len(names), len(paths))
        for required in ('.dockerignore', 'Dockerfile.dashboard', 'compose.dashboard.yaml',
                         'docs/agents.md', 'docs/architecture-roadmap.md',
                         'vendor/dsp/SHA256SUMS', 'vendor/dsp/PROVENANCE.md',
                         'vendor/dsp/licenses/ALSA-LGPL-COPYING', 'web/favicon.svg',
                         'scripts/dashboard-mcp', 'profiles/basic-stereo.json'):
            self.assertIn(required, names)
        self.assertFalse(any(n.startswith(('state/', 'tools/', '.git/')) for n in names))
        self.assertFalse(any('__pycache__' in n or n.endswith('.pyc') for n in names))

    def test_stack_inputs_are_required_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('app.py', 'compose.audio.yaml'):
                (root/name).write_text('fixture\n')
            with patch.object(stage, 'ROOT', root), patch.object(stage, 'INPUTS', ('app.py',)), \
                 patch.object(stage, 'STACK_INPUTS', ('app.py', 'compose.audio.yaml', 'Dockerfile.audio')), \
                 patch.object(stage, 'STACK_REQUIRED', ('app.py',)):
                with self.assertRaisesRegex(SystemExit, 'Missing required input: Dockerfile.audio'):
                    stage.package_paths(SimpleNamespace(docker_stack=True))
                (root/'Dockerfile.audio').write_text('fixture\n')
                self.assertEqual(len(stage.package_paths(SimpleNamespace(docker_stack=True))), 3)

    def test_current_stack_has_hardware_release_assets(self):
        paths = stage.package_paths(SimpleNamespace(docker_stack=True))
        names = {str(p.relative_to(ROOT)) for p in paths}
        self.assertIn('compose.audio-hardware.yaml', names)
        for name in stage.STACK_REQUIRED:
            self.assertIn(name, names)

    def test_future_ma_and_network_recipes_are_inventoried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('app.py', 'Dockerfile.ma', 'compose.namespace.yaml'):
                (root/name).write_text('fixture\n')
            with patch.object(stage, 'ROOT', root), patch.object(stage, 'INPUTS', ('app.py',)), \
                 patch.object(stage, 'STACK_INPUTS', ()), patch.object(stage, 'STACK_REQUIRED', ()):
                names = {p.name for p in stage.package_paths(SimpleNamespace(docker_stack=True))}
                self.assertEqual(names, {'app.py','Dockerfile.ma','compose.namespace.yaml'})

    def test_pending_release_inputs_never_pass_strict_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').mkdir()
            (root/stage.RELEASE_INPUT_MANIFEST).write_text(json.dumps({
                'schema_version':1,'status':'pending_owner_inputs',
                'required_files':['Dockerfile.ma'],'official_bases':{'dashboard':None},
                'owner_manifests':{'runtime_apt_lock':None}}))
            with patch.object(stage, 'ROOT', root):
                result = stage.release_inputs([])
                self.assertFalse(result['ready_for_gate'])
                self.assertIn('owner manifest: runtime_apt_lock', result['unresolved'])
                self.assertEqual(result['unresolved'].count('Dockerfile.ma'), 1)
                with self.assertRaisesRegex(SystemExit, 'Release inputs unresolved'):
                    stage.release_inputs([], strict=True)

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'target').write_text('fixture\n')
            (root/'input').symlink_to(root/'target')
            with patch.object(stage, 'ROOT', root), patch.object(stage, 'INPUTS', ('input',)):
                with self.assertRaisesRegex(SystemExit, 'Symlink input rejected'):
                    stage.package_paths(SimpleNamespace(docker_stack=False))

    def test_combined_candidate_never_dashboard_deploys(self):
        with self.assertRaisesRegex(SystemExit, 'combined audio cutover'):
            stage.deploy(SimpleNamespace(docker_stack=True))


if __name__ == '__main__':
    unittest.main()
