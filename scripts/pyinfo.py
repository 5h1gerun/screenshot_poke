import platform, struct, sys
print(sys.version)
print(platform.architecture())
print(struct.calcsize('P')*8)

