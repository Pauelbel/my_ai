// Скрипт KWin (KDE на Wayland): позиция курсора → приложению по D-Bus, не чаще раза в 40 мс.
// Загружает и выгружает его ui/cursor.py; обычным приложениям Wayland позицию курсора не отдаёт.

const INTERVAL = 40;
let last = 0;
let timer = null;

// ----------------------------------------
// Отправка текущей позиции в org.myai.Cursor
// ----------------------------------------
function send() {
    last = Date.now();
    const p = workspace.cursorPos;
    callDBus("org.myai.Cursor", "/cursor", "org.myai.Cursor", "Move", Math.round(p.x), Math.round(p.y));
}

// ----------------------------------------
// Движение мыши: сразу, если давно не слали; иначе — один отложенный вызов, чтобы не потерять конечную точку
// ----------------------------------------
function moved() {
    const wait = INTERVAL - (Date.now() - last);
    if (wait <= 0) {
        send();
    } else if (timer && !timer.active) {
        timer.interval = wait;
        timer.start();
    }
}

try {
    timer = new QTimer();
    timer.singleShot = true;
    timer.timeout.connect(send);
} catch (error) {
    timer = null;  // нет QTimer — без отложенного вызова, конечная точка может чуть отстать
}
workspace.cursorPosChanged.connect(moved);
send();
