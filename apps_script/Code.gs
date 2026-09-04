// Paste this into the Apps Script editor attached to a Google Sheet.
// Extensions menu -> Apps Script, delete any starter code, paste this in.
//
// This replaces the old aggregate-only version. It now logs every individual
// reaction as its own row (with who tapped it), so you can see who voted for
// each award-winning photo instead of just a total count.

const LOG_SHEET_NAME = "ReactionLog";
const SUMMARY_SHEET_NAME = "Reactions"; // kept for backward-compat totals

function getLogSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(LOG_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(LOG_SHEET_NAME);
    sheet.appendRow(["timestamp", "photoId", "reaction", "voter"]);
  }
  return sheet;
}

function getSummarySheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SUMMARY_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SUMMARY_SHEET_NAME);
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

// GET ?mode=voters&photoId=XYZ  -> list of {reaction, voter, timestamp} for one photo
// GET (no params)               -> aggregate totals per photo, same shape as before
function doGet(e) {
  const params = (e && e.parameter) || {};

  if (params.mode === "voters") {
    const logSheet = getLogSheet_();
    const data = logSheet.getDataRange().getValues();
    const result = [];
    for (let i = 1; i < data.length; i++) {
      const row = data[i];
      const timestamp = row[0], photoId = row[1], reaction = row[2], voter = row[3];
      if (!params.photoId || photoId === params.photoId) {
        result.push({ timestamp: timestamp, photoId: photoId, reaction: reaction, voter: voter });
      }
    }
    return ContentService.createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  }

  // default: aggregate totals, computed live from the log so it's always accurate
  const logSheet = getLogSheet_();
  const data = logSheet.getDataRange().getValues();
  const totals = {};
  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    const photoId = row[1], reaction = row[2];
    if (!totals[photoId]) totals[photoId] = { heart: 0, laugh: 0, thumbsdown: 0 };
    if (totals[photoId][reaction] !== undefined) totals[photoId][reaction]++;
  }
  return ContentService.createTextOutput(JSON.stringify(totals))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const body = JSON.parse(e.postData.contents);
    const photoId = String(body.photoId || "");
    const reaction = String(body.reaction || "");
    const voter = String(body.voter || "Unknown");
    if (!photoId || ["heart", "laugh", "thumbsdown"].indexOf(reaction) === -1) {
      return ContentService.createTextOutput(JSON.stringify({ error: "bad request" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // Log the individual reaction event (this is the new source of truth)
    const logSheet = getLogSheet_();
    logSheet.appendRow([new Date(), photoId, reaction, voter]);

    // Also keep the old aggregate sheet updated, for anything still reading it
    const summarySheet = getSummarySheet_();
    let row = findRow_(summarySheet, photoId);
    if (!row) {
      summarySheet.appendRow([photoId, 0, 0, 0]);
      row = summarySheet.getLastRow();
    }
    const colMap = { heart: 2, laugh: 3, thumbsdown: 4 };
    const col = colMap[reaction];
    const current = summarySheet.getRange(row, col).getValue() || 0;
    summarySheet.getRange(row, col).setValue(current + 1);

    return ContentService.createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}
