from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QLineEdit, QSpinBox, QTextEdit,
                             QProgressBar, QFrame, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from datetime import datetime

from bilibili_spider.spiders.comment_spider import BilibiliSpider
from bilibili_spider.models.comments import Comment

# pages/crawl_page.py 顶部添加导入
import time
import requests
import random


class CrawlWorker(QThread):
    """
    @description: 爬取工作线程，负责在后台执行评论爬取任务
    """
    progress = pyqtSignal(str)  # 用于发送进度信息
    error = pyqtSignal(str)
    comment_received = pyqtSignal(dict)  # 用于发送单条评论数据
    finished = pyqtSignal(dict)

    def __init__(self, spider, url, max_pages):
        """
        @description: 初始化爬取工作线程
        @param {BilibiliSpider} spider - 爬虫实例
        @param {str} url - 要爬取的视频URL
        @param {int} max_pages - 最大爬取页数
        """
        super().__init__()
        self.spider = spider
        self.url = url
        self.max_pages = max_pages
        self.is_running = False

    def run(self):
        """
        @description: 执行爬取任务的主方法
        """
        self.is_running = True
        try:
            video_id = self.spider.extract_video_id(self.url)
            if not video_id:
                self.error.emit("无法从URL中提取视频ID")
                return

            # 获取视频标题
            video_title = ""
            if video_id.startswith('BV'):
                view_url = f'https://api.bilibili.com/x/web-interface/view?bvid={video_id}'
            else:
                view_url = f'https://api.bilibili.com/x/web-interface/view?aid={video_id.lstrip("av")}'

            try:
                response = requests.get(view_url, headers=self.spider.headers)
                response.raise_for_status()
                data = response.json()
                if data['code'] == 0:
                    video_title = data['data']['title']
                    self.progress.emit(f"获取到视频标题: {video_title}")
                else:
                    self.progress.emit(f"获取视频标题失败: {data.get('message', '未知错误')}")
                    return
            except Exception as e:
                self.progress.emit(f"获取视频标题失败: {str(e)}")
                return

            self.progress.emit(f"开始爬取视频 {video_id} 的评论...")
            current_page = 1
            total_comments = 0

            while current_page <= self.max_pages and self.is_running:
                self.progress.emit(f"正在爬取第 {current_page} 页...")

                api_url = self.spider.get_api_url(video_id, current_page)
                if not api_url:
                    break

                try:
                    response = requests.get(api_url, headers=self.spider.headers)
                    response.raise_for_status()
                    data = response.json()

                    if data['code'] != 0:
                        self.progress.emit(f"API返回错误: {data.get('message', '未知错误')}")
                        break

                    replies = data['data'].get('replies', [])
                    if not replies:
                        self.progress.emit("没有更多评论了")
                        break

                    for reply in replies:
                        comment_data = {
                            'video_id': video_id,
                            'video_title': video_title,
                            'comment_id': str(reply['rpid']),
                            'user_name': reply['member']['uname'],
                            'content': reply['content']['message'],
                            'publish_time': datetime.fromtimestamp(
                                reply['ctime']
                            ).strftime('%Y-%m-%d %H:%M:%S'),
                            'like_count': reply['like'],
                            'replies': []
                        }

                        # 处理子回复
                        if reply.get('replies'):
                            for sub_reply in reply['replies']:
                                reply_data = {
                                    'user_name': sub_reply['member']['uname'],
                                    'content': sub_reply['content']['message'],
                                    'time': datetime.fromtimestamp(
                                        sub_reply['ctime']
                                    ).strftime('%Y-%m-%d %H:%M:%S')
                                }
                                comment_data['replies'].append(reply_data)

                        # 发送单条评论信号
                        self.comment_received.emit(comment_data)
                        total_comments += 1
                        self.progress.emit(f"已获取 {total_comments} 条评论")

                except requests.exceptions.RequestException as e:
                    self.progress.emit(f"请求失败: {str(e)}")
                    continue

                current_page += 1
                time.sleep(random.uniform(1, 3))  # 添加随机延迟

            if self.is_running:
                self.finished.emit({
                    'video_id': video_id,
                    'total_comments': total_comments
                })

        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.is_running = False

    def stop(self):
        """
        @description: 停止爬取任务
        """
        self.is_running = False


class StyledFrame(QFrame):
    """
    @description: 自定义样式面板控件
    """
    def __init__(self, title="", parent=None):
        """
        @description: 初始化样式面板
        @param {str} title - 面板标题
        @param {QWidget} parent - 父控件
        """
        super().__init__(parent)
        self.setStyleSheet("""
            StyledFrame {
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 15px;
                margin: 5px;
            }
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(10)

        if title:
            label = QLabel(title)
            label.setStyleSheet("""
                font-size: 16px;
                font-weight: bold;
                color: white;
                padding: 5px;
                margin-bottom: 10px;
            """)
            self.layout.addWidget(label)


class CrawlPage(QWidget):
    """
    @description: 评论爬取页面，提供视频URL输入和爬取控制功能
    """
    def __init__(self, db_handler, config):
        """
        @description: 初始化爬取页面
        @param {DatabaseHandler} db_handler - 数据库处理器实例
        @param {Config} config - 配置管理实例
        """
        super().__init__()
        self.db_handler = db_handler
        self.config = config
        self.crawl_worker = None

        # 初始化爬虫实例
        cookie, _ = self.db_handler.get_valid_cookie()
        if cookie and self.config.set_cookie(cookie):
            self.spider = BilibiliSpider(self.config)
        else:
            self.spider = None

        self.init_ui()

    def init_ui(self):
        """
        @description: 初始化用户界面
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # URL输入区域
        url_frame = StyledFrame("视频URL")
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(5, 5, 5, 5)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入B站视频URL")
        self.url_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                background-color: #1e1e1e;
                color: white;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #0078d4;
            }
        """)

        url_layout.addWidget(self.url_input)
        url_frame.layout.addLayout(url_layout)
        layout.addWidget(url_frame)

        # 爬取控制区域
        control_frame = StyledFrame("爬取控制")
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 5, 5, 5)
        control_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        page_label = QLabel("爬取页数：")
        page_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                padding-right: 10px;
            }
        """)
        page_label.setFixedWidth(80)
        control_layout.addWidget(page_label)

        # 页数输入框 - 修复高度问题
        self.page_spinbox = QSpinBox()
        self.page_spinbox.setRange(1, 1000000)
        self.page_spinbox.setValue(10)
        self.page_spinbox.setMinimumWidth(120)
        self.page_spinbox.setMinimumHeight(27)  # 增加最小高度
        self.page_spinbox.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_spinbox.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.page_spinbox.setStyleSheet("""
            QSpinBox {
                padding: 8px;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                background-color: #2d2d2d;
                color: white;
                font-size: 14px;
                min-height: 27px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 20px;
                height: 18px;
                background: #404040;
                border: none;
                subcontrol-origin: border;
            }
            QSpinBox::up-button {
                subcontrol-position: top right;
            }
            QSpinBox::down-button {
                subcontrol-position: bottom right;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #505050;
            }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
                background: #606060;
            }
        """)

        control_layout.addWidget(self.page_spinbox)
        control_layout.addStretch()

        # 开始爬取按钮
        self.start_button = QPushButton("开始爬取")
        self.start_button.setStyleSheet("""
            QPushButton {
                padding: 8px 30px;
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                min-width: 120px;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #1184db;
            }
            QPushButton:pressed {
                background-color: #006abc;
            }
            QPushButton:disabled {
                background-color: #666666;
            }
        """)

        control_layout.addWidget(self.start_button)
        control_frame.layout.addLayout(control_layout)
        layout.addWidget(control_frame)

        # 日志显示区域
        log_frame = StyledFrame("运行日志")
        log_frame.layout.setContentsMargins(5, 5, 5, 5)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                background-color: #1e1e1e;
                color: white;
                padding: 8px;
                font-size: 14px;
            }
        """)

        log_frame.layout.addWidget(self.log_text)
        layout.addWidget(log_frame)

        # 连接信号
        self.start_button.clicked.connect(self.start_crawl)

    def add_log(self, message):
        """
        @description: 添加日志信息到日志显示区域
        @param {str} message - 日志信息
        """
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def handle_comment(self, comment_data):
        """
        @description: 处理单条评论数据并保存到数据库
        @param {dict} comment_data - 评论数据字典
        """
        try:
            comment = Comment(
                video_id=comment_data['video_id'],
                video_title=comment_data['video_title'],
                comment_id=comment_data['comment_id'],
                user_name=comment_data['user_name'],
                content=comment_data['content'],
                publish_time=comment_data['publish_time'],
                like_count=comment_data['like_count'],
                replies=comment_data['replies']
            )

            result = self.db_handler.save_comment(comment)
            status = "新增" if result == 1 else "更新" if result == 2 else "失败"
            # 输出到日志
            self.add_log(f"[{status}] {comment_data['user_name']}: {comment_data['content']}")
            # 输出到控制台
            print(f"[{status}] {comment_data['user_name']}: {comment_data['content']}")

        except Exception as e:
            self.add_log(f"处理评论失败: {str(e)}")

    def handle_error(self, error_message):
        """
        @description: 处理爬取过程中的错误
        @param {str} error_message - 错误信息
        """
        self.add_log(f"爬取失败: {error_message}")
        QMessageBox.critical(self, "错误", f"爬取过程出错: {error_message}")
        self.start_button.setEnabled(True)

    def handle_crawl_finished(self, result):
        """
        @description: 处理爬取完成事件
        @param {dict} result - 爬取结果数据
        """
        try:
            if not result:
                return

            video_id = result.get('video_id')
            total_comments = result.get('total_comments', 0)

            self.add_log(f"爬取完成! 共获取 {total_comments} 条评论")
            QMessageBox.information(
                self,
                "成功",
                f"成功爬取视频 {video_id} 的评论\n共获取 {total_comments} 条评论"
            )

        except Exception as e:
            self.handle_error(str(e))
        finally:
            self.start_button.setEnabled(True)

    def start_crawl(self):
        """
        @description: 开始爬取评论
        """
        # 重新获取和设置Cookie
        cookie, _ = self.db_handler.get_valid_cookie()
        if cookie and self.config.set_cookie(cookie):
            self.spider = BilibiliSpider(self.config)
        else:
            self.spider = None

        if not self.spider:
            QMessageBox.warning(self, "提示", "请先设置Cookie")
            return

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入视频URL")
            return

        try:
            self.start_button.setEnabled(False)
            self.crawl_worker = CrawlWorker(self.spider, url, self.page_spinbox.value())
            self.crawl_worker.progress.connect(self.add_log)
            self.crawl_worker.error.connect(self.handle_error)
            self.crawl_worker.comment_received.connect(self.handle_comment)
            self.crawl_worker.finished.connect(self.handle_crawl_finished)
            self.crawl_worker.start()

        except Exception as e:
            self.handle_error(str(e))