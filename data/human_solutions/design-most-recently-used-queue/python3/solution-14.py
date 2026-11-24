n = len(num) - 1
while n > 0:
    if int(num[n]) % 2 == 0:
        n -= 1
    else:
        return num[:n+1]
if int(num[0]) % 2 != 0:
    return num[0]
return ""