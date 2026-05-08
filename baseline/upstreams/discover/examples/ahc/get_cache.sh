#!/bin/bash

# Download the cache from Google Drive
wget --no-check-certificate \
  "https://drive.google.com/uc?export=download&id=1bA044QSbhsQWLjgs467ygoCpoxH3NevD" \
  -O cache_ahc.zip

# Unzip the cache
unzip cache_ahc.zip

# Remove the zip file
rm cache_ahc.zip

# Move the cache to the lib/cache directory
mv cache examples/ahc/lib/cache

# Remove the cache_ahc directory
rm -rf cache

# Print a success message
echo "Cache downloaded and extracted successfully to examples/ahc/lib/cache"