---
name: "Thao Bui"
student_id: "123456787"
course_code: "CMPE"
assignment_title: "Exam"
assignment_version: "2B"
source_pdf: "submission-0002.pdf"
score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: 1. (50 points) Minimum score. You don’t have to do anything for this question.
  - Response image: ![q1 response](responses/submission-0002/q1.png)
  - Response text: 
  - Possible points: 
  - Points: 
  - Analysis: 

- q2:
  - Question type: Free Response
  - Prompt: 2. (1 point) if the 16-bit number 0x95B3 is stored big-endian, what will be the first byte in HEX at the address where it is stored? (include the 0x prefix and no leading zeros. use uppercase letters)
  - Response image: ![q2 response](responses/submission-0002/q2.png)
  - Response text: |
    95
  - Possible points: 
  - Points: 
  - Analysis: 

- q3:
  - Question type: Free Response
  - Prompt: 3. (1 point) if the 16-bit number 0xB395 is stored little-endian, what will be the first byte in HEX at the address where it is stored? (include the 0x prefix and no leading zeros. use uppercase letters)
  - Response image: ![q3 response](responses/submission-0002/q3.png)
  - Response text: |
    95
  - Possible points: 
  - Points: 
  - Analysis: 

- q4:
  - Question type: Free Response
  - Prompt: 4. (2 points) the following function clears bit (makes 0) n (where bit 0 is the least significant bit) in a 64-bit page table entry. write the code to replace XXXX (DO NOT USE ANY SPACES!) void clr_pte_bit(uint64_t *pte, n) { *pte = XXXX; } 1
  - Response image: ![q4 response](responses/submission-0002/q4.png)
  - Response text: |
    this Ban
  - Possible points: 
  - Points: 
  - Analysis: 

- q5:
  - Question type: Free Response
  - Prompt: 5. (2 points) What does each level of the Multi-Level Feedback Queue (MLFQ) correspond to?
  - Response image: ![q5 response](responses/submission-0002/q5.png)
  - Response text: |
    no
  - Possible points: 
  - Points: 
  - Analysis: 

- q6:
  - Question type: Free Response
  - Prompt: 6. (2 points) in MLFQ, rule 5 says: "After some time period S, move all the jobs in the system to the topmost queue." What problem does rule 5 prevent? (one word, all lowercase)
  - Response image: ![q6 response](responses/submission-0002/q6.png)
  - Response text: |
    starvation
  - Possible points: 
  - Points: 
  - Analysis: 

- q7:
  - Question type: Free Response
  - Prompt: 7. (2 points) if we have a program with the following lines of code: ... ... char buffer[1024]; int fd = open("/tmp/file", O_RDWR); char *data = mmap(NULL, 1024*1024, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0); Assume all of the above lines and the following lines execute successfully. Write a line of code that will cause a dirty page for /tmp/file. /tmp/file
  - Response image: ![q7 response](responses/submission-0002/q7.png)
  - Response text: |
    /tmp/file
  - Possible points: 
  - Points: 
  - Analysis: 

- q8:
  - Question type: Free Response
  - Prompt: 8. (2 points) if we are running on a Linux machine with 4G of RAM and 1G of swap, what happens when we run the following program? #include <stdio.h> #include <stdlib.h> int main(int argc, char *argv[]) { char *mem[20]; for (int i = 0; i < 20; i++) { mem[i] = malloc(1024*1024*1024); if (mem[i] == NULL) { printf("failed\n"); exit(i+1); } } printf("allocated 20G\n"); exit(0); } no
  - Response image: ![q8 response](responses/submission-0002/q8.png)
  - Response text: |
    no
  - Possible points: 
  - Points: 
  - Analysis:
