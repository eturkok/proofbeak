"""Actual browser qualification against a live service, real workers and signing identity."""

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path


def main():
    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    from proofbeak.interfaces.http import create_app
    from proofbeak.server import load_service
    from proofbeak.studio import StudioProviders, initialize

    reports = Path('reports/workbench')
    reports.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config, token = initialize(root / 'studio')
        os.environ['PROOFBEAK_SIGNING_KEY_FILE'] = str(config.parent / 'signing-private.json')
        service = load_service(config)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(service,studio_providers=StudioProviders(service,config)),
            host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread = threading.Thread(target=server.run,daemon=True)
        thread.start()
        deadline=time.monotonic()+15
        while not server.started and time.monotonic()<deadline:
            time.sleep(.05)
        assert server.started, 'Workspace server did not start'
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch()
                context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/ui/')
                expect(page.get_by_role('heading',name='Welcome back.')).to_be_visible()
                page.get_by_label('Access key',exact=True).fill(token)
                page.get_by_role('button',name='Open workspace').click()
                expect(page.get_by_role('heading',name='Your evidence workspace.')).to_be_visible()
                assert page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length')==0
                assert token not in page.url and token not in page.content()
                cookies=context.cookies()
                assert any(c['name']=='proofbeak_session' and c['httpOnly'] and c['sameSite']=='Strict' for c in cookies)
                page.reload()
                expect(page.get_by_role('heading',name='Your evidence workspace.')).to_be_visible()
                page.screenshot(path=str(reports/'overview-desktop.png'),full_page=True)
                results.append({'case':'login_reload_and_private_browser_session','passed':True})

                page.get_by_role('button',name='Try a local example').click()
                expect(page.get_by_role('heading',name='What should your service do?')).to_be_visible()
                page.get_by_label('Package name',exact=True).fill('Browser acceptance package')
                # Parameterize the committed value through the visual editor, then use three values.
                fixture=page.locator('.builder-section').filter(has=page.get_by_role('heading',name='Test data',exact=True))
                fixture.get_by_role('button',name='Try multiple values').nth(1).click()
                page.get_by_label('Matrix dimensions',exact=True).fill(json.dumps({'value':['hello','Merhaba 🌍','line one\nline two']}))
                before=len(service.history('local')['runs'])
                page.get_by_role('button',name='Validate & preview',exact=True).click()
                expect(page.get_by_role('heading',name='Ready to review',exact=True)).to_be_visible()
                assert len(service.history('local')['runs'])==before
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(reports/'builder-desktop.png'),full_page=False)
                page.get_by_role('button',name='Run test package',exact=True).click()
                expect(page.get_by_role('heading',name='Browser acceptance package',exact=True)).to_be_visible(timeout=20000)
                expect(page.locator('.badge.completed').first).to_be_visible(timeout=20000)
                first=service.list_suites('local')['suites'][0]['suite_id']
                assert service.get_suite('local',first)['gate']=='pass'
                assert len(service.history('local')['runs'])==before+3
                results.append({'case':'visual_parameter_matrix_preview_and_real_sqlite_workers','passed':True,'executions':3})

                page.get_by_role('button',name='Inspect evidence',exact=True).first.click()
                expect(page.get_by_role('heading',name='Independent observation record')).to_be_visible()
                page.get_by_text('Before the action',exact=True).click()
                expect(page.locator('dialog pre').first).to_contain_text('observed_at')
                page.get_by_role('button',name='Close',exact=True).click()
                page.get_by_role('button',name='Export results',exact=True).click()
                with page.expect_download() as downloaded:
                    page.get_by_role('button',name='Signed evidence bundle',exact=True).click()
                bundle_path=root/'record.bundle.json'
                downloaded.value.save_as(str(bundle_path))
                bundle=json.loads(bundle_path.read_text())
                assert bundle['format']=='proofbeak.bundle.v1'
                assert token not in bundle_path.read_text()
                page.get_by_role('button',name='Close dialog',exact=True).click()
                results.append({'case':'inspect_independent_observations_and_signed_export','passed':True})

                # A retry after a lost submission response must retain its key and avoid new writes.
                page.get_by_role('button',name='Test builder',exact=True).click()
                page.get_by_label('Package name',exact=True).fill('Safe retry package')
                page.get_by_role('button',name='Validate & preview',exact=True).click()
                expect(page.get_by_role('heading',name='Ready to review',exact=True)).to_be_visible()
                dropped=[]
                def drop_response(route):
                    if route.request.method=='POST' and not dropped:
                        response=route.fetch()
                        assert response.status==202
                        dropped.append(True)
                        route.abort('failed')
                    else:
                        route.continue_()
                page.route('**/suites',drop_response)
                count=len(service.list_suites('local')['suites'])
                page.get_by_role('button',name='Run test package',exact=True).click()
                expect(page.get_by_text('Submission not confirmed.',exact=True)).to_be_visible()
                assert len(service.list_suites('local')['suites'])==count+1
                page.get_by_role('button',name='Retry submission',exact=True).click()
                expect(page.get_by_role('heading',name='Safe retry package',exact=True)).to_be_visible(timeout=20000)
                expect(page.locator('.badge.completed').first).to_be_visible(timeout=20000)
                assert len(service.list_suites('local')['suites'])==count+1
                page.unroute('**/suites',drop_response)
                results.append({'case':'lost_submission_response_reuses_key_without_replay','passed':True})

                page.get_by_role('button',name='Compare releases',exact=True).click()
                page.get_by_role('button',name='Compare recorded results',exact=True).click()
                expect(page.get_by_role('heading',name='Comparison complete',exact=True)).to_be_visible()
                assert 'unchanged' in page.content()
                results.append({'case':'historical_baseline_comparison','passed':True})

                page.get_by_role('button',name='Verify evidence',exact=True).click()
                page.locator('[data-file="bundle"]').set_input_files(str(bundle_path))
                page.get_by_role('button',name='Use workspace public key',exact=True).click()
                page.get_by_role('button',name='Verify signature & content',exact=True).click()
                expect(page.get_by_role('heading',name='Signature and content verified.',exact=True)).to_be_visible()
                corrupted=json.loads(bundle_path.read_text())
                corrupted['payload']['report']['name']='modified'
                corrupt_path=root/'modified.bundle.json'
                corrupt_path.write_text(json.dumps(corrupted))
                page.locator('[data-file="bundle"]').set_input_files(str(corrupt_path))
                page.get_by_role('button',name='Verify signature & content',exact=True).click()
                expect(page.get_by_role('heading',name='Signature and content verified.',exact=True)).not_to_be_visible()
                expect(page.get_by_text('Verification failed. Check the trusted public key and whether the bundle was modified.',exact=True)).to_be_visible()
                results.append({'case':'pinned_public_key_verification_and_tamper_rejection','passed':True})

                page.get_by_role('button',name='Overview',exact=True).click()
                page.get_by_role('button',name='Configure a service',exact=True).first.click()
                page.locator('#provider-alias').fill('another-sqlite')
                page.locator('#provider-kind').select_option('sqlite')
                page.get_by_role('button',name='Save service profile',exact=True).click()
                expect(page.get_by_text('capability:another-sqlite',exact=False).first).to_be_visible()
                assert 'capability:another-sqlite' in service.packs('local')['providers']
                results.append({'case':'local_owner_service_configuration_is_durable','passed':True})

                # Mobile pages retain a local scrolling table without overflowing the viewport.
                page.set_viewport_size({'width':390,'height':844})
                for name in ('Run history','Compare releases','Verify evidence','Overview'):
                    page.get_by_role('button',name='Toggle navigation',exact=True).click()
                    page.get_by_role('button',name=name,exact=True).click()
                    geometry=page.evaluate('''() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
                        overflowing:[...document.querySelectorAll('body *')].map(e=>({tag:e.tagName,cls:e.className,
                            right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width}))
                            .filter(e=>e.right>innerWidth+1).slice(0,25)})''')
                    if geometry['scroll'] > geometry['width']:
                        (reports/'mobile-layout.json').write_text(json.dumps({'page':name,**geometry},indent=2))
                        page.screenshot(path=str(reports/'failure.png'),full_page=True)
                    assert geometry['scroll'] <= geometry['width'],name
                page.screenshot(path=str(reports/'overview-mobile.png'),full_page=True)
                page.get_by_role('button',name='Toggle navigation',exact=True).click()
                page.get_by_role('button',name='Test builder',exact=True).click()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(reports/'builder-mobile.png'),full_page=True)
                results.append({'case':'responsive_mobile_navigation_and_editor','passed':True,'viewport':'390x844'})
                page.get_by_role('button',name='JSON definition',exact=True).click()
                invalid=service.get_suite('local',first)['definition']
                invalid['cases'][0]['test']['observe']=[{}]
                before=len(service.history('local')['runs'])
                page.locator('#suite-json').fill(json.dumps(invalid))
                page.get_by_role('button',name='Validate & preview',exact=True).click()
                expect(page.get_by_role('heading',name='Let’s fix these details.',exact=True)).to_be_visible()
                assert len(service.history('local')['runs'])==before
                page.get_by_role('button',name='Visual editor',exact=True).click()
                expect(page.locator('#suite-json')).to_be_visible()
                results.append({'case':'incomplete_import_stays_editable_without_provider_work','passed':True})
                assert page.locator('#announcements .toast').count() <= 1
                assert not errors, errors
                context.close()
                browser.close()
        except Exception:
            if 'page' in locals():
                try:
                    page.screenshot(path=str(reports/'failure.png'),full_page=True)
                except Exception:
                    pass
            (reports/'qualification.json').write_text(json.dumps({'passed':False,'completed_cases':results,
                'browser_errors':errors if 'errors' in locals() else []},indent=2)+'\n')
            raise
        finally:
            server.should_exit=True
            thread.join(timeout=10)
            assert not thread.is_alive(),'Workspace server did not shut down'
    output={'suite':'proofbeak.workbench.browser.v1','cases':results,'passed':all(c['passed'] for c in results)}
    (reports/'qualification.json').write_text(json.dumps(output,indent=2)+'\n')
    print('PROOFBEAK_WORKBENCH='+json.dumps(output))


if __name__=='__main__':
    main()
