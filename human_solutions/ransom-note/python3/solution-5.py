def canConstruct(ransomNote, magazine):
    freq = [0] * 26
    
    for char in magazine:
        freq[ord(char) - ord('a')] += 1
    
    for char in ransomNote:
        index = ord(char) - ord('a')
        freq[index] -= 1
        if freq[index] < 0:
            return False
    
    return True