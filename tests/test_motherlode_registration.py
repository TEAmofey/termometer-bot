import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, Chat, User as TelegramUser, CallbackQuery
from handlers import motherlode, registration
from db.user import User
from utils.users import education_selection


class RegistrationGateTests(unittest.IsolatedAsyncioTestCase):
    def message(self):
        return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=123,type='private'),
                       from_user=TelegramUser(id=123,is_bot=False,first_name='Test'), text='/motherlode')

    async def test_command_requires_registration(self):
        state=AsyncMock(); state.get_data.return_value={}
        with patch.object(User,'get_by_tg_id',return_value=User({'tg_id':123})), patch.object(Message,'answer',new_callable=AsyncMock), patch.object(registration,'start_new_registration_flow',new_callable=AsyncMock) as start:
            await motherlode.cmd_motherlode(self.message(),state)
            self.assertTrue(start.call_args.kwargs['existing_data']['resume_motherlode'])
            state.set_state.assert_not_awaited()

    async def test_existing_send_button_keeps_draft_and_does_not_deliver(self):
        state=AsyncMock(); state.get_data.return_value={'motherlode_text':'Нужна помощь','main_message_id':1}
        message=self.message()
        callback=CallbackQuery(id='query',from_user=message.from_user,chat_instance='chat',message=message,data='motherlode_send_request')
        with patch.object(User,'get_by_tg_id',return_value=None), patch.object(Message,'answer',new_callable=AsyncMock), patch.object(registration,'start_new_registration_flow',new_callable=AsyncMock) as start, patch.object(motherlode,'_deliver_motherlode',new_callable=AsyncMock) as deliver:
            await motherlode.cb_motherlode_send(callback,state)
            deliver.assert_not_awaited()
            self.assertEqual(start.call_args.kwargs['existing_data']['motherlode_text'],'Нужна помощь')

    async def test_delivery_itself_rejects_incomplete_profiles(self):
        with patch.object(User,'get_by_tg_id',return_value=User({'tg_id':123})), patch.object(motherlode,'MOTHERLODE_CHAT_ID',456), patch.object(motherlode.bot,'send_message',new_callable=AsyncMock) as send:
            self.assertFalse(await motherlode._deliver_motherlode('text',self.message().from_user))
            send.assert_not_awaited()

    async def test_registration_resumes_draft_without_storing_it(self):
        state=AsyncMock(); state.get_data.return_value=dict(name='Test',direction='Математика',resume_motherlode=True,motherlode_text='Draft',**education_selection('1','bachelor'))
        callback=MagicMock(); callback.from_user.id=123; callback.from_user.username='test'; callback.answer=AsyncMock()
        db=MagicMock(); db.users.find_one.return_value={}
        saved=[]
        with patch.object(registration.Database,'get',return_value=db), patch.object(User,'save_to_db',lambda u:saved.append(u.raw)), patch.object(motherlode,'open_motherlode',new_callable=AsyncMock) as resume, patch.object(registration,'logger'):
            await registration.cb_confirm_registration_final(callback,state)
            resume.assert_awaited_once_with(callback.message,state,'Draft')
        self.assertNotIn('motherlode_text',saved[0])
        self.assertNotIn('resume_motherlode',saved[0])

    async def test_incomplete_confirmation_cannot_save(self):
        state=AsyncMock(); state.get_data.return_value={'name':'Test','direction':'Математика'}
        callback=MagicMock(); callback.from_user.id=123; callback.from_user.username='test'; callback.answer=AsyncMock()
        with patch.object(User,'save_to_db') as save:
            await registration.cb_confirm_registration_final(callback,state)
            save.assert_not_called()
        callback.answer.assert_awaited()
