# RWDExchange

RWDExchange is a Shiny application designed to evaluate the exchangeability potential of your real-world data (RWD) for use as external comparators in clinical studies.

## Features

- **Variable Assessment**: Evaluate the basic variables.
- **Pocock's Criteria**: Assess the criteria for using historical controls.
- **FDA Guidance**: Follow the FDA guidelines for externally controlled trials.
- **Download Report**: Generate a comprehensive report of your assessments.
- **How To**: Instructions on how to use the RWDExchange app.
- **OMOP Data Scan**: The OMOP Data Scan feature is in development.  It will allow for scanning OMOP-formatted data based on pre-scpecified concept codes that represent the variables identified in the other features of the app. Stay tuned for updates!</p>

## Installation and Usage

### Prerequisites

Make sure you have R and RStudio installed. You also need to install the required packages:

```r
install.packages(c("shiny", "devtools"))
```
Download and install Rtools from [here](https://cran.r-project.org/bin/windows/Rtools/).

### Installation

You can install the `RWDExchange` package directly from GitHub:

```r
devtools::install_github("BoyceLab/RWDExchange")
```

### Running the App

Load the package and run the app with the following commands:

```r
library(RWDExchange)
run_app()
```

## Usage Guide

### Step-by-Step Instructions

1. **Variable Assessment**:
   - Navigate to the "Variable Assessment" tab.
   - Enter the project name, clinical trial variable name, RWD variable name, and select the category.
   - Answer the questions about the variable's collection and availability.
   - Add notes as needed and click "Add Variable" to save the variable.

2. **Pocock's Criteria**:
   - Select a variable from the dropdown menu.
   - Evaluate each criterion and provide notes.
   - Click "Save Pocock Criteria" to save your evaluations.

3. **FDA Guidance**:
   - Select a variable from the dropdown menu.
   - Evaluate each criterion and provide notes.
   - Click "Save FDA Criteria" to save your evaluations.

4. **Download Report**:
   - Navigate to the "Download Report" tab.
   - Click "Generate CSV Report" to download a comprehensive report of your assessments.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

# Reference:
- Gray CM, Grimson F, Layton D, Pocock S, Kim J. A Framework for Methodological Choice and Evidence Assessment for Studies Using External Comparators from Real-World Data. Drug Saf. 2020 Jul;43(7):623-633. doi: 10.1007/s40264-020-00944-1. PMID: 32440847; PMCID: PMC7305259.
- Pocock SJ. The combination of randomized and historical controls in clinical trials. J Chronic Dis. 1976 Mar;29(3):175-88. doi: 10.1016/0021-9681(76)90044-8. PMID: 770493.
- US Food and Drug Administration, "Considerations for the Design and Conduct of Externally Controlled Trials for Drug and Biological Products." February 2023. [Link](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/considerations-design-and-conduct-externally-controlled-trials-drug-and-biological-products).


## Contact

Developed by Danielle Boyce, MPH, DPA. For any questions or support, contact danielle@boycedatascience.com.
```
