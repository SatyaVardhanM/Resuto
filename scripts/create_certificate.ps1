# create_certificate.ps1
# Run ONCE on your machine in Admin PowerShell to generate Crazyfolk Labs certificate
# Output:
#   crazyfolk-labs.pfx  -> keep private (add to GitHub Secrets as base64)
#   crazyfolk-labs.cer  -> share publicly (commit to public repo)
#
# Usage:
#   .\scripts\create_certificate.ps1 -Password "your_strong_password"

param(
    [Parameter(Mandatory=$true)]
    [string]$Password,
    [int]$YearsValid = 5
)

$certName   = "Crazyfolk Labs"
$outputPath = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$pfxPath    = Join-Path $outputPath "crazyfolk-labs.pfx"
$cerPath    = Join-Path $outputPath "data\crazyfolk-labs.cer"

Write-Host "`n== Generating Crazyfolk Labs Code Signing Certificate ==" -ForegroundColor Cyan

# Create self-signed code signing certificate
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject "CN=Crazyfolk Labs, O=Crazyfolk Labs, C=US" `
    -KeyUsage DigitalSignature `
    -FriendlyName "Crazyfolk Labs Code Signing" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears($YearsValid)

Write-Host "  Certificate created" -ForegroundColor Green
Write-Host "  Thumbprint: $($cert.Thumbprint)"
Write-Host "  Valid until: $($cert.NotAfter.ToString('yyyy-MM-dd'))"

# Export PFX (private key + cert — keep this secret)
$securePass = ConvertTo-SecureString -String $Password -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $securePass | Out-Null
Write-Host "  PFX saved: $pfxPath" -ForegroundColor Green

# Export CER (public cert only — safe to share)
Export-Certificate -Cert $cert -FilePath $cerPath -Type CERT | Out-Null
Write-Host "  CER saved: $cerPath" -ForegroundColor Green

# Generate base64 of PFX for GitHub Secrets
$pfxBytes  = [System.IO.File]::ReadAllBytes($pfxPath)
$b64Path   = Join-Path $outputPath "crazyfolk-labs.pfx.b64.txt"
[System.Convert]::ToBase64String($pfxBytes) | Out-File -FilePath $b64Path -Encoding ascii
Write-Host "  Base64 saved: $b64Path" -ForegroundColor Green

Write-Host "`n== Next Steps ==" -ForegroundColor Cyan
Write-Host "1. Add to GitHub Secrets:"
Write-Host "     CERT_PFX_BASE64 = contents of crazyfolk-labs.pfx.b64.txt"
Write-Host "     CERT_PASSWORD   = $Password"
Write-Host "2. Commit data\crazyfolk-labs.cer to public repo"
Write-Host "3. Delete crazyfolk-labs.pfx.b64.txt after adding to secrets"
Write-Host "4. Never commit the .pfx file`n"