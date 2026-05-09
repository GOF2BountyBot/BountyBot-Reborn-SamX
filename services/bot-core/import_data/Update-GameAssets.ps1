#Requires -Version 5.1

<#
.SYNOPSIS
    Updates JSON files with correct icon URLs and ship skin data from provided image URLs.

.DESCRIPTION
    This script processes JSON files in the import_data directory structure to:
    1. Update "icon" fields for primary_weapon, turret_weapon, secondary_weapon, and module items
    2. Convert ship "compatibleSkins" from string arrays to dictionaries with image URLs

.PARAMETER ImportDataPath
    Path to the import_data directory containing the JSON files

.PARAMETER ImageUrlsFile
    Path to the text file containing the image URLs

.PARAMETER WhatIf
    Shows what changes would be made without actually modifying files

.EXAMPLE
    .\Update-GameAssets.ps1 -ImportDataPath ".\services\bot-core\import_data" -ImageUrlsFile ".\image_urls.txt"

.EXAMPLE
    .\Update-GameAssets.ps1 -ImportDataPath ".\services\bot-core\import_data" -ImageUrlsFile ".\image_urls.txt" -WhatIf
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ImportDataPath,
    
    [Parameter(Mandatory=$true)]
    [string]$ImageUrlsFile,
    
    [switch]$WhatIf
)

# Function to normalize names for matching
function Get-NormalizedName {
    param([string]$Name)
    
    $normalized = $Name.ToLower()
    
    # Handle some typos first...
    $normalized = $normalized -replace "suukk", "suuk"
    $normalized = $normalized -replace "R.E.D.", "red"
    $normalized = $normalized -replace "é", "e"
    
    # Handle special characters
    $normalized = $normalized -replace "'", "39"  # Replace apostrophes with 39
    $normalized = $normalized -replace "[^a-z0-9]", "-"  # Replace non-alphanumeric with dashes
    $normalized = $normalized -replace "-+", "-"  # Replace multiple dashes with single dash
    $normalized = $normalized -replace "^-|-$", ""  # Remove leading/trailing dashes
    
    return $normalized
}

# Function to find matching URL for an item name
function Find-ItemUrl {
    param(
        [string]$ItemName,
        [hashtable]$ItemUrls
    )
    
    $normalizedName = Get-NormalizedName -Name $ItemName
    
    # Direct match first
    if ($ItemUrls.ContainsKey($normalizedName)) {
        return $ItemUrls[$normalizedName]
    }
    
    # Check for inconsistent `'` handling for ships...
    if ($ItemUrls.ContainsKey(($normalizedName -replace "39", "-"))) {
        return $ItemUrls[($normalizedName -replace "39", "-")]
    }
    
    # Try partial matches
    foreach ($key in $ItemUrls.Keys) {
        if ($key -like "*$normalizedName*" -or $normalizedName -like "*$key*") {
            Write-Host "    Found partial match: '$ItemName' -> '$key'" -ForegroundColor Yellow
            return $ItemUrls[$key]
        }
    }
    
    return $null
}

# Function to find ship skin URL
function Find-ShipSkinUrl {
    param(
        [string]$ShipName,
        [string]$SkinName,
        [hashtable]$ShipSkinUrls
    )
    
    $normalizedShip = Get-NormalizedName -Name $ShipName
    $normalizedSkin = Get-NormalizedName -Name $SkinName
    
    # Handle special skin name mappings
    $skinMappings = @{
        "neapolitan" = "neopolitan"
        "racing stripes" = "racing-stripes"
        "carbon fibre" = "carbon-fibre"
        "leopard print" = "leopard-print"
        "soul marble" = "soul-marble"
        "soul shell" = "soul-shell"
        "urban camo" = "urban-camo"
    }
    
    if ($skinMappings.ContainsKey($normalizedSkin)) {
        $normalizedSkin = $skinMappings[$normalizedSkin]
    }
    
    $key = "$normalizedShip-$normalizedSkin"
    
    if ($ShipSkinUrls.ContainsKey($key)) {
        return $ShipSkinUrls[$key]
    }
    
    return $null
}

# Main script execution
try {
    Write-Host "Starting game assets update..." -ForegroundColor Green
    
    # Validate paths
    if (-not (Test-Path $ImportDataPath)) {
        throw "Import data path not found: $ImportDataPath"
    }
    
    if (-not (Test-Path $ImageUrlsFile)) {
        throw "Image URLs file not found: $ImageUrlsFile"
    }
    
    # Read and parse image URLs
    Write-Host "Reading image URLs..." -ForegroundColor Cyan
    $imageUrls = Get-Content $ImageUrlsFile | Where-Object { $_.Trim() -and $_.StartsWith("https://") }
    Write-Host "Found $($imageUrls.Count) image URLs" -ForegroundColor Green
    
    # Separate ship skin URLs from item URLs
    $shipSkinUrls = @{}
    $itemUrls = @{}
    
    $skinPatterns = @(
        "bloodlust",
        "camo",
        "candy",
        "carbon-fibre",
        "cargo",
        "ferrari",
        "festive",
        "lava",
        "leopard-print",
        "lilac",
        "mint",
        "neopolitan",
        "onyx",
        "racing-stripes",
        "rainbow",
        "rusted",
        "slate",
        "soul-marble",
        "space",
        "tex",
        "urban-camo"
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
    
    Write-Host "Parsed URLs: $($shipSkinUrls.Count) ship skins, $($itemUrls.Count) items" -ForegroundColor Green
    
    # Process weapon and module directories
    $weaponTypes = @("primary_weapon", "turret_weapon", "secondary_weapon", "module")
    $updatedFiles = 0
    $skippedFiles = 0
    <#
    foreach ($weaponType in $weaponTypes) {
        $weaponPath = Join-Path $ImportDataPath $weaponType
        
        if (-not (Test-Path $weaponPath)) {
            Write-Host "Skipping missing directory: $weaponType" -ForegroundColor Yellow
            continue
        }
        
        Write-Host "`nProcessing $weaponType files..." -ForegroundColor Cyan
        $jsonFiles = Get-ChildItem -Path $weaponPath -Filter "*.json"
        
        foreach ($file in $jsonFiles) {
            try {
                $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
                $originalIcon = $json.icon
                $updated = $false
                
                # Find matching URL
                $newUrl = Find-ItemUrl -ItemName $json.name -ItemUrls $itemUrls
                
                if ($newUrl -and $newUrl -ne $originalIcon) {
                    Write-Host "  Updating $($file.Name): $($json.name)" -ForegroundColor White
                    Write-Host "    Old: $originalIcon" -ForegroundColor Red
                    Write-Host "    New: $newUrl" -ForegroundColor Green
                    
                    if (-not $WhatIf) {
                        $json.icon = $newUrl
                        $updated = $true
                    }
                } elseif (-not $newUrl) {
                    Write-Host "  No matching URL found for: $($json.name)" -ForegroundColor Yellow
                    $skippedFiles++
                } else {
                    Write-Host "  Already up to date: $($json.name)" -ForegroundColor Gray
                }
                
                if ($updated -and -not $WhatIf) {
                    $json | ConvertTo-Json -Depth 10 | Set-Content $file.FullName -Encoding UTF8 -Force
                    $updatedFiles++
                }
                
            } catch {
                Write-Host "  Error processing $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }
    #>
    # Process ship files
    $shipPath = Join-Path $ImportDataPath "ship"
    if (Test-Path $shipPath) {
        Write-Host "`nProcessing ship files..." -ForegroundColor Cyan
        $shipFiles = Get-ChildItem -Path $shipPath -Filter "*.json"
        
        foreach ($file in $shipFiles) {
            Write-Host "Processing ship file: $file" -ForegroundColor Cyan
            try {
                $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
                $updated = $false
                # Write-Host ('Checking ($json.compatibleSkins): ' + ($json.compatibleSkins)) -ForegroundColor Cyan
                # Write-Host ('Checking ($json.compatibleSkins -is [Array]): ' + ($json.compatibleSkins -is [Array])) -ForegroundColor Cyan
                # Write-Host ('Checking ($json.compatibleSkins.GetType().FullName): ' + ($json.compatibleSkins.GetType().FullName)) -ForegroundColor Cyan
                if ($json.compatibleSkins -and $json.compatibleSkins -is [PSCustomObject]) {
                    Write-Host "  Processing ship: $($json.name)" -ForegroundColor White
                    
                    $newCompatibleSkins = @{}
                    
                    foreach ($skin in $skinPatterns) {
                        $skinUrl = Find-ShipSkinUrl -ShipName $json.name -SkinName $skin -ShipSkinUrls $shipSkinUrls
                        
                        if ($skinUrl) {
                            $newCompatibleSkins[$skin] = $skinUrl
                            Write-Host "    Found skin: $skin -> $skinUrl" -ForegroundColor Green
                        } else {
                            Write-Host "    Missing skin URL for: $skin" -ForegroundColor Yellow
                            $newCompatibleSkins[$skin] = $null
                        }
                    }
                    
                    if (-not $WhatIf) {
                        $json.compatibleSkins = $newCompatibleSkins
                        $updated = $true
                    }
                }
                
                $originalIcon = $json.icon
                
                # Find matching URL
                $newUrl = Find-ItemUrl -ItemName $json.name -ItemUrls $itemUrls
                
                if ($newUrl -and $newUrl -ne $originalIcon) {
                    Write-Host "  Updating $($file.Name): $($json.name)" -ForegroundColor White
                    Write-Host "    Old: $originalIcon" -ForegroundColor Red
                    Write-Host "    New: $newUrl" -ForegroundColor Green
                    
                    if (-not $WhatIf) {
                        $json.icon = $newUrl
                        $updated = $true
                    }
                } elseif (-not $newUrl) {
                    Write-Host "  No matching URL found for: $($json.name)" -ForegroundColor Yellow
                    $skippedFiles++
                } else {
                    Write-Host "  Already up to date: $($json.name)" -ForegroundColor Gray
                }
                
                if ($updated -and -not $WhatIf) {
                    $json | ConvertTo-Json -Depth 10 | Set-Content $file.FullName -Encoding UTF8 -Force
                    $updatedFiles++
                }
                
                if ($updated -and -not $WhatIf) {
                    $json | ConvertTo-Json -Depth 10 | Set-Content $file.FullName -Encoding UTF8
                    $updatedFiles++
                }
                
            } catch {
                Write-Host "  Error processing ship $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }
    
    # Summary
    Write-Host "`n" + "="*50 -ForegroundColor Green
    Write-Host "Update Summary:" -ForegroundColor Green
    Write-Host "Files updated: $updatedFiles" -ForegroundColor Green
    Write-Host "Files skipped: $skippedFiles" -ForegroundColor Yellow
    
    if ($WhatIf) {
        Write-Host "`nNo changes were made (WhatIf mode)" -ForegroundColor Cyan
    }
    
} catch {
    Write-Host "Script failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "`nScript completed successfully!" -ForegroundColor Green