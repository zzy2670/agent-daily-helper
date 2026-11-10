"""Desktop status and manual controls for the locally scheduled worker."""
from pathlib import Path
import os
import sqlite3
from contextlib import closing
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timezone, timedelta
from settings import is_enabled,set_enabled
from runtime_config import DATA,find_executable,sqlite_readonly_uri

BASE=Path(__file__).resolve().parent
TASK='AgentDaily-Helper'
PYTHONW=Path(sys.executable).with_name('pythonw.exe')
NO_WINDOW=0x08000000
REASONS={
 'today_already_successful':'今天已成功，已跳过重复领取。',
 'today_claimed_visible':'已在加油站确认：今日已领取。',
 'today_claimed_visible_after_click':'领取成功，并已核对页面结果。',
 'client_not_found':'找不到客户端，请按说明安装或配置路径。',
 'login_required':'登录已失效，请点“打开 WorkBuddy”完成登录。',
 'workbuddy_in_foreground_wait_for_user':'您正在使用 WorkBuddy，稍后自动重试。',
 'workbuddy_became_foreground_stop':'WorkBuddy 进入前台，已停止本次操作。',
 'control_channel_unavailable':'当前程序未启用后台控制，请退出后通过桌面的“AgentDaily-打开客户端”重开。',
 'pending_result_not_confirmed_no_second_click':'上次点击结果未确认。为避免重复，今天仅核对状态。',
 'application_startup_error':'WorkBuddy 启动失败，等待下一次重试。',
 'reward_activity_missing_or_obscured':'加油站入口不可用或有未识别弹窗，请在 WorkBuddy 中查看。',
 'isolated_startup_timeout':'后台启动超时，等待下一次重试。',
 'isolated_cleanup_failed':'后台进程退出检查失败，请查看日志。',
 'application_exit_not_confirmed':'积分已领取，但软件尚未确认退出；后续仅重试退出。',
 'no_claim_input_was_dispatched':'尚未点击领取，将在下次触发时重试。',
}

root=tk.Tk()
root.title('WorkBuddy 积分助手')
root.geometry('640x390')
root.minsize(600,360)
style=ttk.Style(root)
style.configure('Title.TLabel',font=('Microsoft YaHei UI',17,'bold'))
frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
ttk.Label(frame,text='WorkBuddy 每日积分',style='Title.TLabel').pack(anchor='w')
ttk.Label(frame,text='用户登录后 · 每天本地时间 05:00 / 13:00',padding=(0,12,0,0)).pack(anchor='w')
ttk.Label(frame,text='当天成功后跳过；失败由计划任务间隔 15 分钟补试，最多 3 次。').pack(anchor='w')
status=tk.StringVar(value='正在读取状态…')
detail=tk.StringVar()
schedule=tk.StringVar()
ttk.Label(frame,textvariable=status,font=('Microsoft YaHei UI',13,'bold'),padding=(0,20,0,8)).pack(anchor='w')
ttk.Label(frame,textvariable=detail,wraplength=570).pack(anchor='w')
ttk.Label(frame,textvariable=schedule,foreground='#555555',padding=(0,12,0,0)).pack(anchor='w')

def start_worker():
    subprocess.Popen([str(PYTHONW),str(BASE/'worker.py'),'--source','desktop'],
        cwd=BASE,creationflags=NO_WINDOW)
    detail.set('正在后台检查；不会移动系统鼠标或切换到 WorkBuddy。')

def open_app():
    try:find_executable()
    except FileNotFoundError:messagebox.showerror('找不到程序','请先安装国内版客户端，或按说明配置路径。');return
    # A user-initiated Open explicitly intends to show the normal application.
    subprocess.Popen([str(PYTHONW),str(BASE/'app_launcher.pyw')],cwd=BASE,creationflags=NO_WINDOW)

def task_info():
    try:
        import win32com.client
        service=win32com.client.Dispatch('Schedule.Service');service.Connect()
        return service.GetFolder('\\').GetTask(TASK)
    except Exception:return None

def toggle():
    set_enabled(not is_enabled())
    refresh(schedule_next=False)

def refresh(schedule_next=True):
    today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    db=DATA/'state.sqlite3'
    try:
        with closing(sqlite3.connect(sqlite_readonly_uri(db),uri=True,timeout=1)) as conn:
            row=conn.execute('SELECT status,updated,detail FROM daily WHERE day=?',(today,)).fetchone()
            run=conn.execute('SELECT time,result,detail FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            pending_exit=conn.execute("SELECT value FROM runtime WHERE key='pending_close'").fetchone()
        status.set('今日已成功领取' if row and row[0]=='success' else
                   '等待确认上次领取结果' if row and row[0]=='pending' else '今天尚未确认领取')
        if run:detail.set(REASONS.get(run[2],'本次未完成，请查看日志了解原因。'))
        if row and row[0]=='success':detail.set('已核对加油站“今日已领取”。今天后续触发会直接跳过。')
        if pending_exit:
            status.set('积分已领取，等待退出 WorkBuddy')
            detail.set('后续只处理该次程序的退出，不会再次领取。')
    except (sqlite3.Error,OSError):status.set('尚无领取记录')
    task=task_info()
    if task:
        enabled=is_enabled()
        schedule.set('自动领取：'+('已启用' if enabled and task.Enabled else '已暂停')+'　　下次触发：'+str(task.NextRunTime))
        pause_button.configure(text='暂停自动领取' if enabled else '启用自动领取')
    else:schedule.set('无法读取计划任务状态，请检查任务是否已安装及访问权限。')
    if schedule_next:root.after(5000,refresh)

buttons=ttk.Frame(frame,padding=(0,22,0,0));buttons.pack(anchor='w')
ttk.Button(buttons,text='现在检查并领取',command=start_worker).pack(side='left',padx=(0,8))
ttk.Button(buttons,text='打开 WorkBuddy',command=open_app).pack(side='left',padx=(0,8))
pause_button=ttk.Button(buttons,text='暂停自动领取',command=toggle);pause_button.pack(side='left')
links=ttk.Frame(frame,padding=(0,12,0,0));links.pack(anchor='w')
ttk.Button(links,text='查看运行日志',command=lambda:os.startfile(DATA/'activity.log') if (DATA/'activity.log').exists() else messagebox.showinfo('运行日志','尚无运行日志。')).pack(side='left',padx=(0,8))
ttk.Button(links,text='打开脚本文件夹',command=lambda:os.startfile(BASE)).pack(side='left')
refresh()
root.mainloop()
