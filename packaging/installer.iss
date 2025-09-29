# Inno Setup script for OBS Screenshot/Template Tool
# Build: iscc.exe packaging\installer.iss /DSourceDir="<dist-folder>"

#define MyAppName "OBS Screenshot/Template Tool"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "YourName"
#ifndef SourceDir
#define SourceDir "dist\\OBS-Screenshot-Tool"
#endif

[Setup]
AppId={{9851A8DB-32EA-4E94-9B4F-6C62E6C6E2C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={pf64}\OBS-Screenshot-Tool
DisableDirPage=no
DisableProgramGroupPage=no
OutputDir=packaging\output
OutputBaseFilename=OBS-Screenshot-Tool-Setup
ArchitecturesInstallIn64BitMode=x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; Flags: unchecked

[Files]
Source: "{#SourceDir}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\\OBS-Screenshot-Tool.exe"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\\OBS-Screenshot-Tool.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\\OBS-Screenshot-Tool.exe"; Description: "{cm:LaunchProgram, {#MyAppName}}"; Flags: nowait postinstall skipifsilent

