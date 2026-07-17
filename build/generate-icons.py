"""Generate placeholder icon assets for Memo app."""
import struct
import zlib
import os

def create_png(width, height, r, g, b):
    sig = b'\x89PNG\r\n\x1a\n'
    
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
    
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00'
        for x in range(width):
            raw_data += bytes([r, g, b])
    
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)
    
    iend_crc = zlib.crc32(b'IEND')
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
    
    return sig + ihdr + idat + iend

os.makedirs('assets', exist_ok=True)

# Tray icon (blue)
with open('assets/tray-icon.png', 'wb') as f:
    f.write(create_png(16, 16, 59, 130, 246))

# Recording icon (red)
with open('assets/tray-icon-recording.png', 'wb') as f:
    f.write(create_png(16, 16, 239, 68, 68))

# ICO file
png_data = create_png(32, 32, 59, 130, 246)
ico_header = struct.pack('<HHH', 0, 1, 1)
ico_entry = struct.pack('<BBBBHHII', 32, 32, 0, 0, 1, 32, len(png_data), 22)
with open('assets/icon.ico', 'wb') as f:
    f.write(ico_header + ico_entry + png_data)

for name in ['tray-icon.png', 'tray-icon-recording.png', 'icon.ico']:
    size = os.path.getsize(f'assets/{name}')
    print(f'  {name}: {size} bytes')

print('All icons generated.')
