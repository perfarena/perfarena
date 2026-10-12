#include <stdio.h>
#include <stdbool.h>

bool is_pangram(char* sentence) {
    int letters[26] = {0};  // Initialize an array to store counts of each letter
    for (int i = 0; sentence[i] != '\0'; i++) {
        if (isalpha(sentence[i])) {
            int index = sentence[i] - 'a';  // Map the character to an index
            letters[index]++;  // Increment the count for the letter
        }
    }
    for (int i = 0; i < 26; i++) {
        if (letters[i] == 0) {
            return false;  // Not a pangram if any count is zero
        }
    }
    return true;  // All counts are greater than zero
}

int main() {
    char sentence[] = "the quick brown fox jumped over the lazy dog";
    printf("%s\n", is_pangram(sentence) ? "True" : "False");  // Output: True
    return 0;
}