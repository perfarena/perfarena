def halvesAreAlike(s: str) -> bool:
    vowels = set('aeiouAEIOU')
    mid = len(s) // 2 - 1
    first_half = second_half = 0
    
    for i, char in enumerate(s):
        if char in vowels:
            if i <= mid:
                first_half += 1
            else:
                second_half += 1
    
    return first_half == second_half