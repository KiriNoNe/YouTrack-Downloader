import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import os
import sys
import subprocess
import urllib.request
from static_ffmpeg import run

PLAYLIST_URL = "https://music.youtube.com/playlist?list=LM"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

PROGRESS_PREFIX = "@@P@@"
DONE_PREFIX = "@@D@@"

COOKIES_HINT = """Как получить cookies.txt

1. Установите в браузер (Chrome / Firefox / Edge) расширение
   "cookies.txt или ему подобное".

2. Войдите в аккаунт Google и откройте:
   https://music.youtube.com

4. Нажмите на иконку расширения и выберите
   "Export" / "Скачать" / "Download" — вы получите файл cookies.txt.

5. Сохраните файл в удобное место и выберите его
   в программе кнопкой "Выбрать cookies".         """


def app_dir() -> str:
    """Папка, рядом с которой лежат yt-dlp.exe, ffmpeg.exe, ffprobe.exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def no_window_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def subprocess_env() -> dict:
    """Окружение, заставляющее дочерний yt-dlp печатать в UTF-8."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env

def decode_line(raw: bytes) -> str:
    """Декодирует строку из yt-dlp, перебирая возможные кодировки."""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("YouTube Music — загрузчик «Понравившихся»")
        self.geometry("820x760")
        self.minsize(720, 640)

        self.cookies_path = ctk.StringVar()
        self.output_path = ctk.StringVar()
        self.is_busy = False
        self.total_tracks = 0
        self.finished_tracks = 0

        # пути к ffmpeg/ffprobe заполнятся в _init_ffmpeg (в фоне)
        self.ffmpeg_path = None
        self.ffprobe_path = None
        self.ffmpeg_ready = False

        self._build_ui()
        self._check_dependencies()

        # static_ffmpeg может скачивать бинарники при первом запуске —
        # делаем это в отдельном потоке, чтобы не замораживать UI
        threading.Thread(target=self._init_ffmpeg, daemon=True).start()

    # ---------------- paths ----------------
    @property
    def ytdlp_path(self) -> str:
        return os.path.join(app_dir(), "yt-dlp.exe")


    def _check_dependencies(self):
        missing = []
        if not os.path.isfile(self.ytdlp_path):
            missing.append("yt-dlp.exe (нажмите «Обновить yt-dlp»)")
        # про ffmpeg не пишем — он проверяется асинхронно и докачается сам
        if missing:
            self.set_status("Не хватает: " + "; ".join(missing))
            self.log("⚠ Отсутствуют компоненты:")
            for m in missing:
                self.log(f"   • {m}")
        else:
            self.set_status("Готов к работе")

    def _show_ffmpeg_progress(self):
        self.ffmpeg_progress.grid()
        self.ffmpeg_progress.start()

    def _hide_ffmpeg_progress(self):
        self.ffmpeg_progress.stop()
        self.ffmpeg_progress.grid_remove()

    def _init_ffmpeg(self):
        """Скачивает ffmpeg и ffprobe при первом запуске (если их нет)."""
        self.after(0, self._show_ffmpeg_progress)
        try:
            self.set_status("Проверяю ffmpeg…")
            self.log("→ Проверяю ffmpeg/ffprobe (скачаю при первом запуске)…")

            ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()

            self.ffmpeg_path = ffmpeg_path
            self.ffprobe_path = ffprobe_path
            self.ffmpeg_ready = True

            self.log(f"✓ ffmpeg готов: {ffmpeg_path}")
            self.set_status("Готов к работе")
        except Exception as e:
            self.log(f"✗ Не удалось получить ffmpeg: {e}")
            self.set_status("ffmpeg недоступен — см. журнал")
            self.after(0, lambda: messagebox.showerror(
                "Ошибка ffmpeg",
                f"Не удалось скачать ffmpeg:\n{e}\n\n"
                "Проверьте подключение к интернету и перезапустите программу.",
            ))
        finally:
            self.after(0, self._hide_ffmpeg_progress)

    # ---------------- UI ----------------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="🎵  YouTube Music Downloader",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, padx=24, pady=(20, 4), sticky="w")

        ctk.CTkLabel(
            self,
            text="Скачивание всех треков из плейлиста «Понравившиеся» в MP3",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray70"),
        ).grid(row=1, column=0, padx=24, pady=(0, 16), sticky="w")

        # --- Cookies ---
        cf = ctk.CTkFrame(self, corner_radius=10)
        cf.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")
        cf.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(cf, text="Cookies:", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, padx=(16, 8), pady=16, sticky="w")

        ctk.CTkEntry(
            cf, textvariable=self.cookies_path,
            placeholder_text="Файл cookies.txt не выбран",
            state="readonly",
        ).grid(row=0, column=1, padx=(0, 8), pady=16, sticky="ew")

        ctk.CTkButton(cf, text="Выбрать cookies", width=140, command=self.pick_cookies) \
            .grid(row=0, column=2, padx=(0, 8), pady=16)

        ctk.CTkButton(
            cf, text="?", width=36, fg_color="transparent",
            border_width=1, command=self.show_cookies_hint,
        ).grid(row=0, column=3, padx=(0, 16), pady=16)

        # --- Output ---
        of = ctk.CTkFrame(self, corner_radius=10)
        of.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")
        of.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(of, text="Папка:", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, padx=(16, 8), pady=16, sticky="w")

        ctk.CTkEntry(
            of, textvariable=self.output_path,
            placeholder_text="Папка для сохранения не выбрана",
            state="readonly",
        ).grid(row=0, column=1, padx=(0, 8), pady=16, sticky="ew")

        ctk.CTkButton(of, text="Выбрать папку", width=140, command=self.pick_output) \
            .grid(row=0, column=2, padx=(0, 16), pady=16)

        # --- Download ---
        self.btn_download = ctk.CTkButton(
            self, text="⬇  Скачать «Понравившиеся»", height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start_download,
        )
        self.btn_download.grid(row=4, column=0, padx=20, pady=(4, 12), sticky="ew")

        # --- Progress ---
        pf = ctk.CTkFrame(self, corner_radius=10)
        pf.grid(row=5, column=0, padx=20, pady=(0, 12), sticky="ew")
        pf.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(pf, text="Готов к работе", anchor="w")
        self.status_label.grid(row=0, column=0, padx=16, pady=(14, 4), sticky="ew")

        self.overall_progress = ctk.CTkProgressBar(pf)
        self.overall_progress.set(0)
        self.overall_progress.grid(row=1, column=0, padx=16, pady=(0, 6), sticky="ew")

        self.file_progress = ctk.CTkProgressBar(pf, height=8)
        self.file_progress.set(0)
        self.file_progress.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="ew")

        # --- Log ---
        self.log_box = ctk.CTkTextbox(
            self, height=180, font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.log_box.grid(row=6, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.grid_rowconfigure(6, weight=1)
        self.log_box.configure(state="disabled")

        # --- Footer ---
        ff = ctk.CTkFrame(self, fg_color="transparent")
        ff.grid(row=7, column=0, padx=20, pady=(0, 16), sticky="ew")
        ff.grid_columnconfigure(0, weight=1)

        self.version_label = ctk.CTkLabel(
            ff, text=self._ytdlp_version_text(),
            anchor="w", text_color=("gray40", "gray70"),
        )
        self.version_label.grid(row=0, column=0, sticky="w")

        # маленький прогрессбар для скачивания ffmpeg (скрыт по умолчанию)
        self.ffmpeg_progress = ctk.CTkProgressBar(
            ff, height=6, width=160, mode="indeterminate",
        )
        self.ffmpeg_progress.grid(row=0, column=1, padx=(8, 8), sticky="e")
        self.ffmpeg_progress.grid_remove()

        self.btn_update = ctk.CTkButton(
            ff, text="Обновить yt-dlp", width=160,
            fg_color="transparent", border_width=1,
            command=self.update_ytdlp,
        )
        self.btn_update.grid(row=0, column=2, sticky="e")

    def _ytdlp_version_text(self) -> str:
        if not os.path.isfile(self.ytdlp_path):
            return "yt-dlp: не установлен"
        try:
            res = subprocess.run(
                [self.ytdlp_path, "--version"],
                capture_output=True, text=True, timeout=10,
                env=subprocess_env(),
                creationflags=no_window_flags(),
            )
            ver = (res.stdout or "").strip() or "?"
            return f"yt-dlp: {ver}"
        except Exception:
            return "yt-dlp: ?"

    # ---------------- actions ----------------
    def pick_cookies(self):
        path = filedialog.askopenfilename(
            title="Выберите файл cookies.txt",
            filetypes=[("Cookies", "*.txt"), ("Все файлы", "*.*")],
        )
        if path:
            self.cookies_path.set(path)

    def pick_output(self):
        path = filedialog.askdirectory(title="Выберите папку для сохранения")
        if path:
            self.output_path.set(path)

    def show_cookies_hint(self):
        win = ctk.CTkToplevel(self)
        win.title("Как получить cookies.txt")
        win.geometry("580x460")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win, text="Как получить cookies.txt",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(padx=20, pady=(20, 8), anchor="w")

        txt = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=12))
        txt.pack(padx=20, pady=(0, 12), fill="both", expand=True)
        txt.insert("1.0", COOKIES_HINT)
        txt.configure(state="disabled")

        ctk.CTkButton(win, text="Понятно", command=win.destroy).pack(pady=(0, 20))

    # ---------------- helpers ----------------
    def log(self, msg):
        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _append)

    def set_status(self, text):
        self.after(0, lambda: self.status_label.configure(text=text))

    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.after(0, lambda: self.btn_download.configure(state=state))
        self.after(0, lambda: self.btn_update.configure(state=state))

    # ---------------- update yt-dlp ----------------
    def update_ytdlp(self):
        if self.is_busy:
            return
        self.is_busy = True
        self.set_busy(True)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            self.set_status("Скачиваю свежий yt-dlp.exe…")
            self.log("→ Загрузка: " + YTDLP_URL)

            tmp = self.ytdlp_path + ".part"
            with urllib.request.urlopen(YTDLP_URL, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        frac = got / total
                        self.after(0, lambda fr=frac: self.file_progress.set(fr))

            os.replace(tmp, self.ytdlp_path)
            self.after(0, lambda: self.file_progress.set(0))
            self.log(f"✓ yt-dlp обновлён: {self.ytdlp_path}")
            self.after(0, lambda: self.version_label.configure(
                text=self._ytdlp_version_text()))
            self.set_status("yt-dlp готов — можно скачивать")
        except Exception as e:
            self.log(f"✗ Не удалось обновить yt-dlp: {e}")
            self.set_status("Ошибка обновления — см. журнал")
        finally:
            self.is_busy = False
            self.set_busy(False)

    # ---------------- download ----------------
    def start_download(self):
        if self.is_busy:
            return
        if not os.path.isfile(self.ytdlp_path):
            messagebox.showerror(
                "yt-dlp не найден",
                "Сначала нажмите «Обновить yt-dlp» внизу окна.",
            )
            return
        if not self.ffmpeg_ready or not self.ffmpeg_path:
            messagebox.showerror(
                "ffmpeg ещё не готов",
                "Подождите пару секунд — программа скачивает ffmpeg "
                "при первом запуске. Следите за строкой статуса.",
            )
            return
        cookies = self.cookies_path.get()
        if not cookies or not os.path.isfile(cookies):
            messagebox.showerror("Ошибка", "Сначала выберите файл cookies.txt")
            return
        out = self.output_path.get()
        if not out or not os.path.isdir(out):
            messagebox.showerror("Ошибка", "Сначала выберите папку для сохранения")
            return

        self.is_busy = True
        self.set_busy(True)
        self.finished_tracks = 0
        self.total_tracks = 0
        self.after(0, lambda: self.overall_progress.set(0))
        self.after(0, lambda: self.file_progress.set(0))
        self.after(0, lambda: self.btn_download.configure(text="Скачивание…"))
        threading.Thread(target=self._download_worker,
                         args=(cookies, out), daemon=True).start()

    def _download_worker(self, cookies, out):
        try:
            # 1) Считаем треки
            self.set_status("Получаю список треков…")
            self.log("→ Запрашиваю список «Понравившихся»…")
            count_cmd = [
                self.ytdlp_path,
                "--cookies", cookies,
                "--flat-playlist",
                "--print", "%(id)s",
                "--no-warnings",
                "--encoding", "utf-8",
                PLAYLIST_URL,
            ]
            res = subprocess.run(
                count_cmd, capture_output=True,
                env=subprocess_env(),
                creationflags=no_window_flags(), timeout=120,
            )
            stdout = decode_line(res.stdout or b"")
            ids = [l for l in stdout.splitlines() if l.strip()]
            self.total_tracks = len(ids)
            if self.total_tracks == 0:
                self.log("✗ Список пуст. Проверьте cookies — возможно, они просрочены.")
                self.set_status("Плейлист пуст или cookies недействительны")
                return
            self.log(f"→ Найдено треков: {self.total_tracks}")
            self.set_status(f"Скачивание 0 / {self.total_tracks}")

            # 2) Качаем
            outtmpl = os.path.join(out, "%(playlist_index)03d - %(title)s.%(ext)s")
            cmd = [
                self.ytdlp_path,
                "--cookies", cookies,
                "--ffmpeg-location", os.path.dirname(self.ffmpeg_path),
                "-x", "--audio-format", "mp3",
                "--audio-quality", "192K",
                "--embed-thumbnail", "--add-metadata",
                "--ignore-errors",
                "--no-warnings",
                "--newline", "--progress",
                "--progress-template",
                f"download:{PROGRESS_PREFIX}%(progress.downloaded_bytes)s %(progress.total_bytes)s",
                "--print", f"after_move:{DONE_PREFIX}%(title)s",
                "--encoding", "utf-8",
                "-o", outtmpl,
                PLAYLIST_URL,
            ]

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0,                       # читаем сырые байты
                env=subprocess_env(),
                creationflags=no_window_flags(),
            )

            for raw in iter(proc.stdout.readline, b""):
                line = decode_line(raw).rstrip("\r\n")
                if not line:
                    continue
                if line.startswith(PROGRESS_PREFIX):
                    self._handle_progress_line(line[len(PROGRESS_PREFIX):])
                elif line.startswith(DONE_PREFIX):
                    title = line[len(DONE_PREFIX):].strip()
                    self.finished_tracks += 1
                    self._handle_done(title)
                else:
                    self.log("  " + line)

            proc.wait()

            if self.finished_tracks == 0:
                self.log("✗ Ничего не скачано. Скорее всего, cookies просрочены.")
                self.set_status("Ничего не скачано — обновите cookies")
            else:
                self.set_status(f"Готово: скачано {self.finished_tracks} треков")
                self.log(f"✓ Готово. Всего файлов: {self.finished_tracks}")
                self.after(0, lambda: self.overall_progress.set(1))

        except Exception as e:
            self.log(f"✗ Ошибка: {e}")
            self.set_status("Ошибка — подробности в журнале")
        finally:
            self.is_busy = False
            self.set_busy(False)
            self.after(0, lambda: self.btn_download.configure(
                text="⬇  Скачать «Понравившиеся»"))

    def _handle_progress_line(self, payload: str):
        parts = payload.split()
        if len(parts) < 2:
            return
        try:
            d = int(parts[0]) if parts[0] != "NA" else 0
            t = int(parts[1]) if parts[1] != "NA" else 0
        except ValueError:
            return
        if t <= 0:
            return
        frac = min(d / t, 1.0)
        self.after(0, lambda f=frac: self.file_progress.set(f))
        overall = (self.finished_tracks + frac) / max(self.total_tracks, 1)
        self.after(0, lambda o=overall: self.overall_progress.set(min(o, 0.999)))

    def _handle_done(self, title: str):
        self.after(0, lambda: self.file_progress.set(0))
        n = self.finished_tracks
        self.set_status(f"Скачивание {n} / {self.total_tracks}")
        overall = n / max(self.total_tracks, 1)
        self.after(0, lambda o=overall: self.overall_progress.set(o))
        self.log(f"  ✓ {title}")


if __name__ == "__main__":
    App().mainloop()