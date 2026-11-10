#include <iostream>
#include <vector>
#include <string>

bool isPangram(std::string sentence) {
    std::vector<bool> alphabet(26, false);

    for (char c : sentence) {
        if (isalpha(c)) {
            int position = tolower(c) - 'a';
            alphabet[position] = true;
        }
    }

    for (bool val : alphabet) {
        if (!val) {
            return false;
        }
    }

    return true;
}

int main() {
    std::string sentence = "The quick brown fox jumps over the lazy dog";
    
    if (isPangram(sentence)) {
        std::cout << "The sentence is a pangram." << std::endl;
    } else {
        std::cout << "The sentence is not a pangram." << std::endl;
    }

    return 0;
}