/**
 * בוט הלימודים · סריקת מייל יומית
 *
 * רץ בחשבון Google של עודד (Apps Script), פעם ביום ב-06:30.
 * מחפש ב-Gmail מיילים מהאוניברסיטה מהיומיים האחרונים, ושולח כל מייל חדש לבוט דרך Upstash.
 * הבוט מסכם אותם בהודעת הבוקר (7:44) ומציע מטלות ואירועים, באישור בלבד.
 *
 * מה יוצא מהחשבון: רק מיילים שעונים על החיפוש למטה (שולח, נושא, תאריך, עד 3,000 תווים מהגוף, שמות קבצים מצורפים).
 *
 * הקמה (פעם אחת):
 *   1. Project Settings ← Script properties ← Add:
 *        UPSTASH_URL   = אותו ערך כמו הסוד UPSTASH_URL ב-GitHub
 *        UPSTASH_TOKEN = אותו ערך כמו הסוד UPSTASH_TOKEN ב-GitHub
 *   2. למעלה בוחרים את הפונקציה install ולוחצים Run. מאשרים גישה ל-Gmail (Google יזהיר שהאפליקציה לא מאומתת:
 *      Advanced ← Go to project. זה הקוד שלך, בחשבון שלך).
 *   3. זהו. install מריץ סריקה ראשונה ומתקין הרצה יומית.
 */

// אפשר לשנות את החיפוש ב-Script properties (QUERY). ברירת המחדל:
const DEFAULT_QUERY =
  'newer_than:2d -in:sent -in:chats -category:promotions -category:social ' +
  '(from:runi.ac.il OR from:idc.ac.il OR from:moodle OR from:yedion ' +
  'OR subject:(מטלה OR הגשה OR בחינה OR מבחן OR בוחן OR שיעור OR תרגיל OR ציון OR קורס OR סמסטר))';
const KEY = 'study-bot:mail';

function install() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('scan').timeBased().everyDays(1).atHour(6).nearMinute(30).inTimezone('Asia/Jerusalem').create();
  scan();
  Logger.log('הותקן. הסריקה תרוץ כל יום ב-06:30.');
}

function scan() {
  const props = PropertiesService.getScriptProperties();
  const url = (props.getProperty('UPSTASH_URL') || '').trim().replace(/^"|"$/g, '').replace(/\/$/, '');
  const token = (props.getProperty('UPSTASH_TOKEN') || '').trim().replace(/^"|"$/g, '');
  if (!url || !token) throw new Error('חסרים UPSTASH_URL או UPSTASH_TOKEN ב-Script properties');
  const query = props.getProperty('QUERY') || DEFAULT_QUERY;
  const seen = JSON.parse(props.getProperty('SEEN') || '[]');
  const since = Date.now() - 2 * 24 * 3600 * 1000;
  const out = [];

  GmailApp.search(query, 0, 40).forEach(function (thread) {
    thread.getMessages().forEach(function (m) {
      const id = m.getId();
      if (seen.indexOf(id) >= 0 || m.getDate().getTime() < since) return;
      seen.push(id);
      out.push({
        id: id,
        from: m.getFrom(),
        subject: m.getSubject(),
        date: m.getDate().toISOString(),
        body: (m.getPlainBody() || '').replace(/\n{3,}/g, '\n\n').slice(0, 3000),
        attachments: m.getAttachments({ includeInlineImages: false }).slice(0, 5).map(function (a) { return a.getName(); })
      });
    });
  });

  if (out.length) {
    const cmds = out.slice(0, 25).map(function (x) { return ['RPUSH', KEY, JSON.stringify(x)]; });
    cmds.push(['LTRIM', KEY, '-60', '-1']);
    const r = UrlFetchApp.fetch(url + '/pipeline', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token }, payload: JSON.stringify(cmds)
    });
    if (r.getResponseCode() !== 200) {
      throw new Error('Upstash: ' + r.getResponseCode() + ' ' + r.getContentText().slice(0, 200));
    }
  }
  props.setProperty('SEEN', JSON.stringify(seen.slice(-300)));
  Logger.log('נשלחו ' + out.length + ' מיילים');
}
