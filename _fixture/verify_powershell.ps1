#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'Collect-UrlFiles.ps1') -CoreOnly

$script:Assertions = 0
function Assert-Equal {
    param($Expected, $Actual, [string]$Description)
    if ($Expected -cne $Actual) {
        throw "$Description -- expected '$Expected', got '$Actual'."
    }
    $script:Assertions++
}

$positive = @(
    'see https://example.com/path?q=1',
    'HTTP://localhost:8080',
    'ftp://host/archive',
    'file:///C:/docs/report.txt',
    'contact example.com or a.b.EXAMPLE.MUSEUM',
    ('host.' + ('a' * 63)),
    'address 0.0.0.0',
    'address 255.255.255.255:65535',
    'address 192.168.001.5:80'
)
foreach ($text in $positive) {
    Assert-Equal $true (Test-UrlCollectorText $text) "Must match: $text"
}
$negative = @(
    '', 'ordinary text without an address', 'http//localhost',
    'example.c', ('host.' + ('a' * 64)), '-bad.example',
    'under_score.example', 'example.123', 'bad.example-',
    '256.1.2.3', '1.2.999.4', '1.2.3.4.5',
    'x10.0.0.1', '10.0.0.1x', '1.2.3'
)
foreach ($text in $negative) {
    Assert-Equal $false (Test-UrlCollectorText $text) "Must not match: $text"
}

function Write-Fixture {
    param([string]$Root, [string]$Relative, [byte[]]$Bytes)
    $path = Join-Path $Root $Relative
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($path))
    [System.IO.File]::WriteAllBytes($path, $Bytes)
}

$work = Join-Path $PSScriptRoot ('powershell-' + [guid]::NewGuid().ToString('N'))
try {
    $source = Join-Path $work 'source'
    $output = Join-Path $source 'collected'
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    Write-Fixture $source 'nested/urls.txt' ($utf8.GetBytes('https://example.com/one'))
    Write-Fixture $source 'addresses.log' ($utf8.GetBytes('server 192.168.0.1:8080'))
    Write-Fixture $source 'domain.cfg' ($utf8.GetBytes('service=example.org'))
    Write-Fixture $source 'gbk.txt' ([byte[]](@(0xD6, 0xD0, 0xCE, 0xC4, 32) + $utf8.GetBytes('example.cn')))
    Write-Fixture $source 'latin.txt' ([byte[]](@(0xFF, 32) + $utf8.GetBytes('ftp://localhost/a')))
    Write-Fixture $source '[brackets]/notes.txt' ($utf8.GetBytes('file:///C:/report'))
    Write-Fixture $source 'example.com.txt' ($utf8.GetBytes('ordinary text'))
    Write-Fixture $source 'plain.txt' ($utf8.GetBytes('invalid 300.1.2.3'))
    Write-Fixture $source 'image.PNG' ($utf8.GetBytes('https://example.com'))
    Write-Fixture $source 'nul.txt' ([byte[]]($utf8.GetBytes('example.com') + @(0)))
    Write-Fixture $source 'oversize.txt' ([byte[]]::new(5000001))

    $events = [System.Collections.Generic.List[object]]::new()
    $preview = Invoke-UrlFileCollection -Source $source -Output $output -DryRun -OnEvent {
        param($event)
        $events.Add($event)
    }
    Assert-Equal 11 $preview.Scanned 'Preview scans every source file'
    Assert-Equal 6 $preview.Hits 'Preview matches content and fallback encodings'
    Assert-Equal 0 $preview.Copied 'Preview never copies'
    Assert-Equal 3 $preview.Skipped 'Binary, NUL and oversized files are skipped'
    Assert-Equal 0 $preview.Errors 'Valid fixture has no errors'
    Assert-Equal $false ([System.IO.Directory]::Exists($output)) 'Preview creates no output directory'
    Assert-Equal 6 @($events | Where-Object { $_.Kind -eq 'Hit' -and $_.Action -eq 'Preview' }).Count 'Preview lists every planned copy'

    $first = Invoke-UrlFileCollection -Source $source -Output $output
    Assert-Equal 11 $first.Scanned 'Copy excludes the newly created output subtree'
    Assert-Equal 6 $first.Hits 'Copy has the same hits as preview'
    Assert-Equal 6 $first.Copied 'Copy writes matching files'
    Assert-Equal 3 $first.Skipped 'Copy skips unsupported files'
    Assert-Equal 0 $first.Errors 'Copy completes without errors'
    foreach ($relative in @('nested/urls.txt', 'addresses.log', 'domain.cfg', 'gbk.txt', 'latin.txt', '[brackets]/notes.txt')) {
        $expected = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes((Join-Path $source $relative)))
        $actual = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes((Join-Path $output $relative)))
        Assert-Equal $expected $actual "Copy preserves bytes and relative path: $relative"
    }
    Write-Fixture $output 'do-not-scan.txt' ($utf8.GetBytes('extra.example'))
    $second = Invoke-UrlFileCollection -Source $source -Output $output
    Assert-Equal 11 $second.Scanned 'Existing output files are excluded'
    Assert-Equal 6 $second.Hits 'Duplicate files still count as hits'
    Assert-Equal 0 $second.Copied 'Same size and SHA256 are never copied twice'
    Assert-Equal 9 $second.Skipped 'Duplicate hits count as skipped'
}
finally {
    if ([System.IO.Directory]::Exists($work)) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}

Write-Output "PASS: $script:Assertions assertions."
