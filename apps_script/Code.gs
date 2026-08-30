// Paste this into the Apps Script editor attached to a Google Sheet.
// Extensions menu -> Apps Script, delete any starter code, paste this in.

const SHEET_NAME = "Reactions";

function getSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(["photoId", "heart", "laugh", "thumbsdown"]);
  }
  return sheet;
}

function findRow_(sheet, photoId) {
  const data = sheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === photoId) return i + 1;
  }
  return null;
}

function doGet(e) {
  const sheet = getSheet_();
  const data = sheet.getDataRange().getValues();
  const result = {};
  for (let i = 1; i < data.length; i++) {
    result[data[i][0]] = { heart: data[i][1] || 0, laugh: data[i][2] || 0, thumbsdown: data[i][3] || 0 };
  }
  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const body = JSON.parse(e.postData.contents);
    const photoId = String(body.photoId || "");
    const reaction = String(body.reaction || "");
    if (!photoId || ["heart", "laugh", "thumbsdown"].indexOf(reaction) === -1) {
      return ContentService.createTextOutput(JSON.stringify({ error: "bad request" }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const sheet = getSheet_();
    let row = findRow_(sheet, photoId);
    if (!row) {
      sheet.appendRow([photoId, 0, 0, 0]);
      row = sheet.getLastRow();
    }
    const colMap = { heart: 2, laugh: 3, thumbsdown: 4 };
    const col = colMap[reaction];
    const current = sheet.getRange(row, col).getValue() || 0;
    sheet.getRange(row, col).setValue(current + 1);
    return ContentService.createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}
