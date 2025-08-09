#Requires -Version 5.1

<#
.SYNOPSIS
    Validates and tests the game assets update process before running the main script.

.DESCRIPTION
    This helper script performs validation checks and provides utilities for testing the
    Update-GameAssets.ps1 script before making actual changes.

.PARAMETER ImportDataPath
    Path to the import_data directory containing the JSON files

.PARAMETER ImageUrlsFile
    Path to the text file containing the image URLs

.EXAMPLE
    .\Test-GameAssets.ps1 -ImportDataPath ".\services\bot-core\import_data" -ImageUrlsFile ".\image_urls.txt"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ImportDataPath,
    
    [Parameter(Mandatory=$true)]
    [string]$ImageUrlsFile
)

# Function to normalize names for matching (same as main script)
function Get-NormalizedName {
    param([string]$Name)
    
    $normalized = $Name.ToLower()
    $normalized = $normalized -replace "'", "39"
    $normalized = $normalized -replace "[^a-z0-9]", "-"
    $normalized = $normalized -replace "-+", "-"
    $normalized = $normalized -replace "^-|-$", ""
    
    return $normalized
}

try {
    Write-Host "Game Assets Validation Report" -ForegroundColor Green
    Write-Host "="*50 -ForegroundColor Green
    
    # Validate paths
    if (-not (Test-Path $ImportDataPath)) {
        throw "Import data path not found: $ImportDataPath"
    }
    
    if (-not (Test-Path $ImageUrlsFile)) {
        throw "Image URLs file not found: $ImageUrlsFile"
    }
    
    # Read image URLs
    $imageUrls = Get-Content $ImageUrlsFile | Where-Object { $_.Trim() -and $_.StartsWith("https://") }
    Write-Host "Total image URLs found: $($imageUrls.Count)" -ForegroundColor Green
    
    # Parse URLs
    $shipSkinUrls = @{}
    $itemUrls = @{}
    
    $skinPatterns = @(
        "abyss", "aperture", "blackout", "bloodlust", "camo", "candy", 
        "carbon-fibre", "cargo", "ferrari", "festive", "lava", 
        "leopard-print", "lilac", "mint", "neopolitan", "onyx", 
        "racing-stripes", "rainbow", "rusted", "slate", "soul-marble", 
        "soul-shell", "space", "tex", "urban-camo"
    )
    
    foreach ($url in $imageUrls) {
        $filename = [System.IO.Path]::GetFileNameWithoutExtension(($url -split '/')[-1])
        
        $isShipSkin = $false
        foreach ($pattern in $skinPatterns) {
            if ($filename -like "*-$pattern") {
                $shipSkinUrls[$filename] = $url
                $isShipSkin = $true
                break
            }
        }
        
        if (-not $isShipSkin) {
            $itemUrls[$filename] = $url
        }
    }
    
    Write-Host "Ship skin URLs: $($shipSkinUrls.Count)" -ForegroundColor Cyan
    Write-Host "Item URLs: $($itemUrls.Count)" -ForegroundColor Cyan
    
    # Test weapon/module files
    Write-Host "`nWeapon/Module File Analysis:" -ForegroundColor Yellow
    $weaponTypes = @("primary_weapon", "turret_weapon", "secondary_weapon", "module")
    $totalItems = 0
    $matchedItems = 0
    
    foreach ($weaponType in $weaponTypes) {
        $weaponPath = Join-Path $ImportDataPath $weaponType
        
        if (Test-Path $weaponPath) {
            $jsonFiles = Get-ChildItem -Path $weaponPath -Filter "*.json"
            Write-Host "`n$weaponType ($($jsonFiles.Count) files):" -ForegroundColor White
            
            foreach ($file in $jsonFiles) {
                try {
                    $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
                    $totalItems++
                    
                    $normalizedName = Get-NormalizedName -Name $json.name
                    $hasMatch = $itemUrls.ContainsKey($normalizedName)
                    
                    if ($hasMatch) {
                        $matchedItems++
                        Write-Host "  ✓ $($json.name) -> $normalizedName" -ForegroundColor Green
                    } else {
                        Write-Host "  ✗ $($json.name) -> $normalizedName (no match)" -ForegroundColor Red
                        
                        # Look for potential partial matches
                        $partialMatches = $itemUrls.Keys | Where-Object { $_ -like "*$normalizedName*" -or $normalizedName -like "*$_*" }
                        if ($partialMatches) {
                            Write-Host "    Potential matches: $($partialMatches -join ', ')" -ForegroundColor Yellow
                        }
                    }
                } catch {
                    Write-Host "  Error reading $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
                }
            }
        } else {
            Write-Host "$weaponType directory not found" -ForegroundColor Red
        }
    }
    
    # Test ship files
    Write-Host "`nShip File Analysis:" -ForegroundColor Yellow
    $shipPath = Join-Path $ImportDataPath "ship"
    
    if (Test-Path $shipPath) {
        $shipFiles = Get-ChildItem -Path $shipPath -Filter "*.json"
        Write-Host "Ship files found: $($shipFiles.Count)" -ForegroundColor White
        
        foreach ($file in $shipFiles) {
            try {
                $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
                
                if ($json.compatibleSkins) {
                    Write-Host "`n$($json.name):" -ForegroundColor White
                    
                    foreach ($skin in $json.compatibleSkins) {
                        $normalizedShip = Get-NormalizedName -Name $json.name
                        $normalizedSkin = Get-NormalizedName -Name $skin
                        
                        # Handle special skin mappings
                        $skinMappings = @{
                            "neapolitan" = "neopolitan"
                            "racing-stripes" = "racing-stripes"
                            "carbon-fibre" = "carbon-fibre"
                            "leopard-print" = "leopard-print"
                            "soul-marble" = "soul-marble"
                            "soul-shell" = "soul-shell"
                            "urban-camo" = "urban-camo"
                        }
                        
                        if ($skinMappings.ContainsKey($normalizedSkin)) {
                            $normalizedSkin = $skinMappings[$normalizedSkin]
                        }
                        
                        $key = "$normalizedShip-$normalizedSkin"
                        
                        if ($shipSkinUrls.ContainsKey($key)) {
                            Write-Host "  ✓ $skin -> $key" -ForegroundColor Green
                        } else {
                            Write-Host "  ✗ $skin -> $key (no match)" -ForegroundColor Red
                        }
                    }
                }
            } catch {
                Write-Host "Error reading ship $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }
    
    # Summary
    Write-Host "`n" + "="*50 -ForegroundColor Green
    Write-Host "Validation Summary:" -ForegroundColor Green
    Write-Host "Total weapon/module items: $totalItems" -ForegroundColor White
    Write-Host "Items with matching URLs: $matchedItems" -ForegroundColor Green
    Write-Host "Match rate: $([math]::Round(($matchedItems / $totalItems) * 100, 1))%" -ForegroundColor Cyan
    
    if ($matchedItems -lt $totalItems) {
        Write-Host "`nRecommendation: Review unmatched items above before running the update script." -ForegroundColor Yellow
    } else {
        Write-Host "`nAll items have matching URLs! Ready to run the update script." -ForegroundColor Green
    }
    
} catch {
    Write-Host "Validation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "`nValidation completed!" -ForegroundColor Green