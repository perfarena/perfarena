int sIndex = 0;
int eIndex = s.Length - 1;
HashSet<char> vowels = new HashSet<char> { 'a', 'e', 'i', 'o', 'u' };
int vowelCount = 0;

while (sIndex < eIndex) {
    if (vowels.Contains(char.ToLower(s[sIndex]))) {
        vowelCount++;
    }
    if (vowels.Contains(char.ToLower(s[eIndex]))) {
        vowelCount--;
    }
    sIndex++;
    eIndex--;
}

return vowelCount == 0;