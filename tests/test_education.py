import unittest
from datetime import datetime
from unittest.mock import patch, AsyncMock
from aiogram.types import User as TelegramUser
from db.user import User
from db.database import UsersRepository
from handlers.admin import _render_user_details
from handlers.registration import get_display_profile_text, cb_graduation_select, process_graduation, cb_confirm_registration_final
from handlers.events.common import user_track
from handlers import motherlode
from scripts.promote_students_2026 import promoted_profile
from scripts.migrate_education_years import migrate_profile
from utils.users import (education_selection, education_course, education_status,
                         current_academic_year, LEGACY_EDUCATION_FIELDS)


class EducationTests(unittest.TestCase):
    def profile(self, course='1', track='master'):
        return dict(tg_id=123, name='Test', direction='Современная математика' if track == 'master' else 'Математика',
                    **education_selection(course, track, academic_year=2026))

    def test_all_courses_and_rollovers(self):
        for track, duration in [('bachelor', 4), ('master', 2)]:
            for course in range(1, duration + 1):
                doc = self.profile(str(course), track)
                year = 2026 + duration + 1 - course
                self.assertEqual(doc['education']['graduation_year'], year)
                self.assertEqual(education_course(doc, academic_year=2026), course)
                self.assertEqual(education_course(doc, academic_year=year-1), duration)
                self.assertEqual(education_status(doc, academic_year=year), 'graduate')
                self.assertEqual(education_status(doc, academic_year=year+5), 'graduate')
                self.assertIsNone(education_course(doc, academic_year=year))

    def test_timezone_boundary(self):
        self.assertEqual(current_academic_year(datetime.fromisoformat('2027-07-31T20:59:59+00:00')), 2026)
        self.assertEqual(current_academic_year(datetime.fromisoformat('2027-07-31T21:00:00+00:00')), 2027)
        self.assertEqual(current_academic_year(datetime.fromisoformat('2027-01-01T00:00:00+00:00')), 2026)

    def test_rendering_everywhere(self):
        doc = self.profile('2')
        with patch('utils.users.current_academic_year', return_value=2026):
            self.assertEqual(User(doc).get_course(), 2)
            self.assertTrue(User(doc).is_registration_complete())
            for text in (get_display_profile_text(doc), _render_user_details(User(doc))):
                self.assertIn('Курс:</b> 2', text)
        with patch('utils.users.current_academic_year', return_value=2027):
            self.assertTrue(User(doc).is_registration_complete())
            self.assertEqual(user_track(User(doc)), 'master')
            for text in (get_display_profile_text(doc), _render_user_details(User(doc))):
                self.assertIn('Выпускник', text)
                self.assertNotIn('Курс:', text)

    def test_postgraduate_and_unknown_graduation(self):
        doc = dict(name='Test', direction='Аспирантура', **education_selection('2024', 'postgraduate'))
        self.assertEqual(doc['education']['master_graduation_year'], 2024)
        self.assertIsNone(doc['education']['graduation_year'])
        self.assertTrue(User(doc).is_registration_complete())
        self.assertIn('Год окончания магистратуры:</b> 2024', get_display_profile_text(doc))
        doc = self.profile('Выпускник')
        self.assertIsNone(doc['education']['graduation_year'])
        self.assertEqual(education_status(doc), 'graduate')
        doc.update(education_selection('1', 'master'))
        self.assertEqual(education_status(doc), 'student')

    def test_migration(self):
        for track, direction, duration in [('master', 'Современная математика', 2), ('bachelor', 'Математика', 4)]:
            for course in range(1, duration+1):
                old = dict(name='Test', direction=direction, magistracy_graduation_year=str(course), academic_year=2026)
                new, reason = migrate_profile(old)
                self.assertEqual(reason, 'migrated')
                self.assertEqual(education_course(new, academic_year=2026), course)
                self.assertFalse(any(k in new for k in LEGACY_EDUCATION_FIELDS))
                self.assertIsNone(migrate_profile(new)[0])
                self.assertIsNone(promoted_profile(new, '')[0])
        old.update(education_status='graduate', magistracy_graduation_year='', promotion_2026={'previous_course':'4'})
        new, _ = migrate_profile(old)
        self.assertEqual(new['education']['graduation_year'], 2026)
        self.assertEqual(education_status(new, academic_year=2026), 'graduate')
        old = dict(direction='Современная математика', magistracy_graduation_year='1', registration_completed_at='2026-09-01T00:00:00+03:00')
        self.assertEqual(migrate_profile(old)[0]['education']['graduation_year'], 2028)
        old['registration_completed_at'] = '2025-11-01T00:00:00+03:00'
        self.assertEqual(migrate_profile(old)[1], 'needs_review')

    def test_repository_cleans_legacy_fields(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        doc = self.profile(); doc.update(magistracy_graduation_year='1', academic_year=2026, education_status='student')
        result = UsersRepository(db).save(doc)
        self.assertFalse(any(k in result for k in LEGACY_EDUCATION_FIELDS))
        self.assertEqual(result['education']['graduation_year'], 2028)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_buttons_and_text(self):
        from unittest.mock import MagicMock
        for track, values in [('master', ['1', '2', 'Выпускник']), ('bachelor', ['1', '2', '3', '4', 'Выпускник'])]:
            for index, value in enumerate(values):
                state = AsyncMock(); state.get_data.return_value = {'main_message_id':1, 'direction_track':track}
                callback = MagicMock(); callback.data = f'graduation_select:{index}'; callback.answer = AsyncMock()
                with patch('handlers.registration.show_confirmation', new_callable=AsyncMock):
                    await cb_graduation_select(callback, state)
                self.assertEqual(state.update_data.call_args.kwargs, education_selection(value, track))
                state.reset_mock()
                message = MagicMock(); message.text = value; message.delete = AsyncMock()
                with patch('handlers.registration.show_confirmation', new_callable=AsyncMock):
                    await process_graduation(message, state)
                self.assertEqual(state.update_data.call_args.kwargs, education_selection(value, track))

    async def test_confirmation_preserves_known_year_and_changes_stage(self):
        from unittest.mock import MagicMock
        existing = dict(tg_id=123, name='Test', direction='Математика',
                        education={'stage':'bachelor', 'graduation_year':2026},
                        thermometer={'enabled':True}, registration_completed_at='2025-11-01T00:00:00+00:00')
        for direction, selection, expected in [
            ('Математика', education_selection('Выпускник', 'bachelor'), existing['education']),
            ('Современная математика', education_selection('1', 'master', academic_year=2026), {'stage':'master', 'graduation_year':2028}),
        ]:
            state = AsyncMock(); state.get_data.return_value = dict(name='Test', direction=direction, **selection)
            callback = MagicMock(); callback.from_user.id=123; callback.from_user.username='test'
            callback.answer=AsyncMock(); callback.message.answer=AsyncMock()
            database = MagicMock(); database.users.find_one.return_value=existing
            saved=[]
            with patch('utils.users.current_academic_year', return_value=2026), patch('handlers.registration.Database.get', return_value=database), patch.object(User, 'save_to_db', lambda user: saved.append(user.raw)), patch('handlers.registration.logger'):
                await cb_confirm_registration_final(callback, state)
            self.assertEqual(saved[0]['education'], expected)
            self.assertTrue(saved[0]['thermometer']['enabled'])
            self.assertEqual(saved[0]['registration_completed_at'], existing['registration_completed_at'])
            self.assertFalse(any(k in saved[0] for k in LEGACY_EDUCATION_FIELDS))

    async def test_multipart_dynamic_status(self):
        doc = dict(name='Test', direction='Математика', **education_selection('4', 'bachelor', academic_year=2026))
        author = TelegramUser(id=123, is_bot=False, first_name='Test')
        for year, expected in [(2026, 'Курс:</b> 4'), (2027, 'Выпускник')]:
            with patch('utils.users.current_academic_year', return_value=year), patch.object(motherlode.User, 'get_by_tg_id', return_value=User(doc)), patch.object(motherlode, 'MOTHERLODE_CHAT_ID', 456), patch.object(motherlode.bot, 'send_message', new_callable=AsyncMock) as send:
                self.assertTrue(await motherlode._deliver_motherlode('<&> text ' * 2000, author))
                self.assertGreater(send.call_count, 1)
                for call in send.call_args_list:
                    text = call.kwargs['text']
                    self.assertIn(expected, text)
                    self.assertLessEqual(len(text), 4096)
