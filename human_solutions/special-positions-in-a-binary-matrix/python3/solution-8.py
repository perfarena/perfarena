def halvesAreAlike(s):
    vowels = set('aeiouAEIOU')
    count = 0
    a, b = s[:len(s)//2], s[len(s)//2:]

    for c in a:
        if c in vowels:
            count += 1

    for c in b:
        if c in vowels:
            count -= 1

    return count == 0

s = "book"
if halvesAreAlike(s):
    print("The halves are alike.")
else:
    print("The halves are not alike.")