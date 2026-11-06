"""Facebook enters the same database queue/cards as other sources; no live provider calls."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock

_tmp = tempfile.TemporaryDirectory()
for key, value in {'DATA_DIR': _tmp.name, 'DB_PATH': _tmp.name+'/workflow.db', 'UPLOAD_DIR': _tmp.name+'/uploads',
                   'RENDER_DIR': _tmp.name+'/renders', 'TEMPLATE_DIR': _tmp.name+'/templates', 'DISABLE_SCHEDULER': 'true'}.items():
    os.environ[key] = value
from app import db, main, sources, facebook_reader as reader, facebook_apify as apify, pipeline


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.exec_('DELETE FROM articles')
        db.exec_('DELETE FROM candidates')
        db.exec_('UPDATE sources SET active=0')
        db.exec_("DELETE FROM sources WHERE url='https://facebook.com/fixture.page'")
        self.sid = db.exec_("INSERT INTO sources(name,url,kind,official,active,local_scope,brand) VALUES(?,?,?,?,?,?,?)",
                            ('Fixture brotherhood', 'https://facebook.com/fixture.page', 'social', 1, 1, 1, 'cofrade'))
        self.post = {'id': '123:456', 'page_id': '123', 'source_id': self.sid,
                     'title': 'Convocatoria de cultos de la hermandad', 'url': 'https://facebook.com/123/posts/456',
                     'published_at': (datetime.now(timezone.utc)-timedelta(days=6)).isoformat(),
                     'text': 'Texto de la fuente para preparar la noticia.', 'image': 'https://example.org/photo.jpg'}
        self.result = {'provider': 'apify', 'items': [self.post], 'pages': [dict(source_id=self.sid, status='ok', name='Fixture brotherhood', count=1)]}
        db.set_setting('facebook_apify_run', '{}')

    def import_post(self):
        with patch.object(reader, 'recent', return_value=self.result), patch.object(sources, 'after_new'):
            return reader.import_recent()

    def test_six_day_post_is_in_normal_queue_with_photo_and_full_text(self):
        result = self.import_post()
        self.assertEqual(len(result['added']), 1)
        queue = main._sort_queue('cofrade')
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['image_hint'], self.post['image'])
        self.assertEqual(queue[0]['title'], self.post['title'])
        self.assertEqual(queue[0]['raw_text'], self.post['text'])
        self.assertEqual(queue[0]['facebook_post_id'], '456')
        sources.archive_stale()
        sources.archive_off_topic()
        self.assertEqual(len(main._sort_queue('cofrade')), 1)

    def test_reimport_does_not_duplicate_or_reset_editorial_decision(self):
        cid = self.import_post()['added'][0]
        db.exec_("UPDATE candidates SET editorial_priority='no_interest',status='archived' WHERE id=?", (cid,))
        self.assertEqual(self.import_post()['added'], [])
        self.assertEqual(db.row('SELECT COUNT(*) n FROM candidates')['n'], 1)
        self.assertEqual(db.row('SELECT editorial_priority FROM candidates WHERE id=?', (cid,))['editorial_priority'], 'no_interest')

    def test_unverified_legacy_or_revoked_source_never_enters_queue(self):
        self.import_post()
        db.exec_("UPDATE candidates SET facebook_post_id=NULL")
        self.assertEqual(main._sort_queue('cofrade'), [])
        db.exec_("UPDATE candidates SET facebook_post_id='456'")
        db.exec_('UPDATE sources SET official=0 WHERE id=?', (self.sid,))
        self.assertEqual(main._sort_queue('cofrade'), [])

    def test_more_than_seven_days_is_not_imported(self):
        self.post['published_at'] = (datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
        self.assertEqual(self.import_post()['added'], [])

    def test_research_uses_imported_text_without_fetching_facebook_html(self):
        cid = self.import_post()['added'][0]
        with patch.object(sources, 'fetch_article_text') as fetch, patch.object(pipeline.ai, 'research', return_value={'sources': []}) as research:
            pipeline.investigate_candidate(cid)
        fetch.assert_not_called()
        self.assertEqual(research.call_args.args[1], self.post['text'])

    def test_background_job_imports_the_exact_run_into_normal_queue(self):
        from app import facebook_jobs as jobs
        jobs._lock.acquire()
        with patch.object(reader, 'configuration', return_value={'provider': 'apify'}), patch.object(apify, 'run_task', return_value='run123') as run, patch.object(reader, 'import_recent', return_value={'added': [99], 'errors': []}) as import_posts:
            jobs._run(lambda: {'added': [100], 'errors': []})
        import_posts.assert_called_once_with(run_id='run123')
        self.assertEqual(run.call_args.args[0], ['https://facebook.com/fixture.page'])
        self.assertEqual(jobs.state['added'], 2)
        self.assertFalse(jobs.state['busy'])
        self.assertFalse(jobs._lock.locked())

    def test_manual_run_starts_saved_task_with_only_approved_urls_and_persists_id(self):
        response = Mock(status_code=201)
        response.json.return_value = {'data': {'id': 'run123'}}
        env = {'APIFY_TOKEN': 'fixture-secret', 'APIFY_FACEBOOK_TASK_ID': 'task123'}
        with patch.dict(os.environ, env), patch.object(apify.requests, 'post', return_value=response) as post, patch.object(apify, '_get', return_value={'data': {'status': 'SUCCEEDED'}}):
            run = apify.run_task(['https://facebook.com/fixture.page']*2, Mock())
        self.assertEqual(run, 'run123')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['json']['startUrls'], [{'url': 'https://facebook.com/fixture.page'}])
        self.assertEqual(post.call_args.kwargs['json']['resultsLimit'], 100)
        self.assertEqual(post.call_args.kwargs['params']['maxTotalChargeUsd'], 1)
        self.assertIn('onlyPostsNewerThan', post.call_args.kwargs['json'])
        with patch.dict(os.environ, env), patch.object(apify.requests, 'post') as post, patch.object(apify, '_get', return_value={'data': {'status': 'SUCCEEDED'}}):
            self.assertEqual(apify.run_task(['https://facebook.com/fixture.page'], Mock()), run)
        post.assert_not_called()

    def test_unknown_start_is_not_retried_as_another_paid_run(self):
        db.set_setting('facebook_apify_run', '{"task":"task123","id":"pending"}')
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture', 'APIFY_FACEBOOK_TASK_ID': 'task123'}), patch.object(apify.requests, 'post') as post, self.assertRaisesRegex(apify.meta.SocialError, 'sin confirmar'):
            apify.run_task(['https://facebook.com/fixture.page'], Mock())
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
