sum = 0
Max = 0

for i in range(n):
    sum += altitude[i]
    
    if sum > Max:
        Max = sum

print(Max)