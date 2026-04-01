<b>Вопрос от студента</b>
Студент: {{student_name}}
Занятие: {{event_title}} ({{start_time}})
ID брони: <code>{{booking_id}}</code>

{{question}}

<i>Telegram-уведомление только для просмотра.</i>
<i>Ответ отправляйте через REST API Bot Manager:</i>
<code>POST /api/tutor/reply</code>
<i>Передайте booking_id=<code>{{booking_id}}</code> и текст ответа.</i>
