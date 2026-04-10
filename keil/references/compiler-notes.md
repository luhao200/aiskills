# Keil MDK 编译器参考

## UV4.exe 命令行参数

| 参数 | 说明 |
|------|------|
| `-b <project>` | 增量编译 |
| `-r <project>` | 全量重建 |
| `-c <project>` | 清理 |
| `-cr <project>` | 先清理再重建 |
| `-f <project>` | 烧录（Flash Download） |
| `-t <target>` | 指定 Target 名称 |
| `-j0` | 不显示 UV4 GUI 窗口 |
| `-o <logfile>` | 输出日志到文件 |

## ERRORLEVEL 返回码

| 返回码 | 含义 |
|--------|------|
| 0 | 无错误，无警告 |
| 1 | 有警告 |
| 2 | 有错误 |
| 3 | 致命错误（license / 工程损坏等） |
| 11 | 无法打开工程文件 |
| 12 | 设备数据库缺失 |
| 13 | 写入错误 |
| 15 | UV4 访问错误（被占用等） |
| 20 | 未知错误 |

## 日志中常见摘要行格式

```
".\Objects\project.axf" - 0 Error(s), 3 Warning(s).
Program Size: Code=12345 RO-data=678 RW-data=90 ZI-data=1234
```

- **Flash 占用** = Code + RO-data + RW-data
- **RAM 占用** = RW-data + ZI-data

## 常见编译错误排查

| 错误类型 | 可能原因 |
|----------|----------|
| `error: #5: cannot open source input file` | 文件路径不存在或包含配置缺少 include 路径 |
| `Error: L6218E: Undefined symbol` | 链接阶段缺少源文件或库 |
| `*** TOOLS.INI: TOOLCHAIN NOT INSTALLED` | Keil 工具链未安装或 license 异常 |
| `*** error 65: access violation` | Flash 算法不匹配目标芯片 |
| `No Algorithm found for` | 工程配置中未选择 Flash 算法 |
