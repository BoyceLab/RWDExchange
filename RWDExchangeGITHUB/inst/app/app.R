library(shiny)
library(shinydashboard)
library(DT)
library(dplyr)
library(shinyWidgets)
library(shinyBS)
library(shinyjs)

ui <- dashboardPage(
  title = "RWDExchange",
  dashboardHeader(
    title = HTML('<h1 style="font-weight: bold; font-size: 30px;">RWDExchange</h1>')
  ),
  dashboardSidebar(
    width = 250,
    tags$div(
      style = "padding: 10px;",
      tags$p(tags$strong("Systematically evaluate the exchangeability potential of your real world data for use as external comparators"), 
             style = "font-size: 16px; font-style: italic; color: #000000;")
    ),
    sidebarMenu(
      id = "tabs",
      menuItem("Variable Assessment", tabName = "variable_assessment", icon = icon("table")),
      menuItem("Pocock's Criteria", tabName = "pocock_criteria", icon = icon("list")),
      menuItem("FDA Guidance", tabName = "fda_guidance", icon = icon("list-alt")),
      menuItem("Download Report", tabName = "save_load", icon = icon("save")),
      menuItem("OMOP Data Scan", tabName = "omop_data_scan", icon = icon("search")),
      menuItem("How To", tabName = "how_to", icon = icon("question-circle")),
      tags$div(
        style = "margin-top: 20px; padding: 10px; font-size: 14px;",
        tags$p("Developed by Danielle Boyce, MPH, DPA:", tags$br(), "danielle@boycedatascience.com", style = "font-size: 14px; color: #000000;")
      )
    )
  ),
  dashboardBody(
    useShinyjs(),
    tags$head(
      tags$style(HTML("
        body, .content-wrapper, .main-sidebar {
          font-size: 18px;
          background-color: #E6E6FA !important; /* Light purple */
          color: #000000 !important; /* Black font */
        }
        .box {
          background-color: #F3E5F5 !important; /* Very light purple */
          color: #000000 !important; /* Black font */
        }
        .box-title {
          font-size: 20px;
          color: #000000 !important; /* Black font */
        }
        .box-header {
          background-color: #D8BFD8 !important; /* Thistle */
          color: #000000 !important; /* Black font */
        }
        .box-footer {
          background-color: #E6E6FA !important; /* Light purple */
          color: #000000 !important; /* Black font */
        }
        .dataTables_wrapper .dataTables_paginate, 
        .dataTables_wrapper .dataTables_filter, 
        .dataTables_wrapper .dataTables_info, 
        .dataTables_wrapper .dataTables_length {
          font-size: 18px;
          color: #000000 !important; /* Black font */
        }
        .dataTables_wrapper .dataTables_processing {
          font-size: 18px;
          color: #000000 !important; /* Black font */
        }
        .shiny-download-link {
          font-size: 18px;
          color: #000000 !important; /* Black font */
        }
        .form-control, .btn {
          font-size: 18px;
          color: #000000 !important; /* Black font */
        }
        .main-header .logo, .main-header .navbar {
          background-color: #D8BFD8 !important; /* Thistle */
          color: #000000 !important; /* Black font */
        }
        .main-sidebar .sidebar-menu > li > a {
          color: #000000 !important; /* Black font */
          background-color: #E6E6FA !important; /* Light purple background */
        }
        .main-sidebar .sidebar-menu > li > a:hover {
          background-color: #D8BFD8 !important; /* Thistle on hover */
          color: #000000 !important; /* Black font */
        }
        .main-sidebar .sidebar-menu > li.active > a {
          background-color: #D8BFD8 !important; /* Thistle for active item */
          color: #000000 !important; /* Black font */
        }
      "))
    ),
    tabItems(
      tabItem(tabName = "variable_assessment",
              fluidRow(
                box(title = tags$div(
                  tags$h3("Step 1: Basic Variable Assessment", style = "color: red; font-weight: bold;")
                ),
                status = "primary", solidHeader = TRUE, width = 12,
                textInput("project_name", "Project name", ""),
                textInput("variable_name", "Enter Clinical Trial (CT) variable name", ""),
                textInput("rwd_variable_name", "Enter Real World Data (RWD) variable name", ""),
                selectInput("variable_category", "Category", choices = c("Inclusion Criteria", "Exclusion Criteria", "Outcome", "Other")),
                radioButtons("variable_collected_identically", "Is the variable in the RWD comparator data set collected identically to the CT?", choices = c("Yes", "No")),
                textAreaInput("collected_identically_notes", "Notes", "", rows = 3, placeholder = "Enter notes about this criterion"),
                radioButtons("variable_fully_available", "Is the variable fully available for all patients in the comparator data set?", choices = c("Yes", "No")),
                textAreaInput("fully_available_notes", "Notes", "", rows = 3, placeholder = "Enter notes about this criterion"),
                radioButtons("variable_completely_missing", "Is the variable completely missing in the comparator data set?", choices = c("Yes", "No")),
                textAreaInput("completely_missing_notes", "Notes", "", rows = 3, placeholder = "Enter notes about this criterion"),
                radioButtons("variable_misclassification", "Is the variable prone to misclassification or measurement error?", choices = c("Yes", "No")),
                textAreaInput("misclassification_notes", "Notes", "", rows = 3, placeholder = "Enter notes about this criterion"),
                radioButtons("variable_insufficient", "Is there anything else about the way this variable is collected in the RWD that may make it inappropriate for use as a comparator?", choices = c("Yes", "No")),
                textAreaInput("insufficient_notes", "Notes", "", rows = 3, placeholder = "Enter notes about this criterion"),
                textAreaInput("variable_notes", "General comments and observations", "", rows = 5, placeholder = "Enter up to 250 words"),
                actionButton("add_variable", "Add Variable"),
                actionButton("reset_variable", "Reset Variable"),
                tags$p(style = "color: red;", "When you are done adding variables, click on the Pocock and FDA tabs to the left to complete your assessment, and then download your report."),
                tags$p(style = "font-size: smaller; font-style: italic;",
                       "Reference:  Gray CM, Grimson F, Layton D, Pocock S, Kim J. A Framework for Methodological Choice and Evidence Assessment for Studies Using External Comparators from Real-World Data. Drug Saf. 2020 Jul;43(7):623-633. doi: 10.1007/s40264-020-00944-1. PMID: 32440847; PMCID: PMC7305259.")
                )
              ),
              fluidRow(
                box(title = "Variables Table", status = "primary", solidHeader = TRUE, width = 12,
                    dataTableOutput("variables_table")
                )
              )
      ),
      tabItem(tabName = "pocock_criteria",
              fluidRow(
                box(title = tags$div(
                  tags$h3("Select Variable (Variables added in the Variable Assessment tab will appear here)", style = "color: red; font-weight: bold;")
                ),
                status = "primary", solidHeader = TRUE, width = 12,
                selectInput("selected_variable_pocock", "Select Variable", choices = NULL)
                )
              ),
              fluidRow(
                box(title = "Pocock's Criteria", status = "primary", solidHeader = TRUE, width = 12,
                    uiOutput("pocock_criteria_ui"),
                    actionButton("save_pocock", "Save Pocock Criteria")
                )
              ),
              fluidRow(
                column(12, tags$br()),
                column(12, tags$p(style = "font-size: smaller; font-style: italic;",
                                  "Reference: Pocock SJ. The combination of randomized and historical controls in clinical trials. J Chronic Dis. 1976 Mar;29(3):175-88. doi: 10.1016/0021-9681(76)90044-8. PMID: 770493.",
                                  tags$br(),
                                  "\"The acceptability of a historical control group requires that it meets the following conditions: 
                                  1. Such a group must have received a precisely defined standard treatment which must be the same as the treatment for the randomized controls. 
                                  2. The group must have been part of a recent clinical study which contained the same requirements for patient eligibility. 
                                  3. The methods of treatment evaluation must be the same. 
                                  4. The distributions of important patient characteristics in the group should be comparable with those in the new trial. 
                                  5. The previous study must have been performed in the same organization with largely the same clinical investigators. 
                                  6. There must be no other indications leading one to expect differing results between the randomized and historical controls. For instance, more rapid accrual on the new study might lead one to suspect less enthusiastic participation of investigators in the previous study so that the process of patient selection may have been different.
                                  Only if all these conditions are met can one safely use the historical controls as part of a randomized trial. Otherwise, the risk of a substantial bias occurring in treatment comparisons cannot be ignored.\""))
              )
      ),
      tabItem(tabName = "fda_guidance",
              fluidRow(
                box(title = tags$div(
                  tags$h3("Select Variable (Variables added in the Variable Assessment tab will appear here)", style = "color: red; font-weight: bold;")
                ),
                status = "primary", solidHeader = TRUE, width = 12,
                selectInput("selected_variable_fda", "Select Variable", choices = NULL)
                )
              ),
              fluidRow(
                box(title = "FDA Guidance", status = "primary", solidHeader = TRUE, width = 12,
                    uiOutput("fda_guidance_ui"),
                    actionButton("save_fda", "Save FDA Criteria")
                )
              ),
              fluidRow(
                column(12, tags$p(style = "font-size: smaller; font-style: italic;",
                                  "US Food and Drug Administration, \"Considerations for the Design and Conduct of Externally Controlled Trials for Drug and Biological Products.\" February 2023",
                                  tags$a(href = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/considerations-design-and-conduct-externally-controlled-trials-drug-and-biological-products", "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/considerations-design-and-conduct-externally-controlled-trials-drug-and-biological-products")))
              )
      ),
      tabItem(tabName = "save_load",
              fluidRow(
                box(title = "Download Report", status = "primary", solidHeader = TRUE, width = 12,
                    downloadButton("downloadCSVReport", "Generate CSV Report")
                )
              )
      ),
      tabItem(tabName = "omop_data_scan",
              fluidRow(
                box(title = "OMOP Data Scan", status = "primary", solidHeader = TRUE, width = 12,
                    tags$p("OMOP Data Scan", tags$span(style = "color: red; font-style: italic;", "(Coming soon!)"))
                )
              )
      ),
      tabItem(tabName = "how_to",
              fluidRow(
                box(title = "How To Use the RWDExchange App", status = "primary", solidHeader = TRUE, width = 12,
                    tags$div(
                      tags$h3("Privacy Assurance"),
                      tags$p("We prioritize your privacy and the confidentiality of the data you enter. All information entered into the RWDExchange app is processed locally and is not stored or transmitted to any external servers. We do not retain any of the information you input."),
                      tags$h3("Introduction"),
                      tags$p("The RWDExchange app helps you systematically evaluate the exchangeability potential of your real-world data (RWD) for use as external comparators in clinical studies. Follow these steps to make the most out of the app."),
                      tags$h3("Navigation Menu"),
                      tags$p("The app consists of several sections accessible from the navigation menu on the left:"),
                      tags$ul(
                        tags$li(tags$strong("Variable Assessment")),
                        tags$li(tags$strong("Pocock's Criteria")),
                        tags$li(tags$strong("FDA Guidance")),
                        tags$li(tags$strong("Download Report")),
                        tags$li(tags$strong("OMOP Data Scan")),
                        tags$li(tags$strong("How To") , "(This Guide)")
                      ),
                      tags$h3("Step 1: Variable Assessment"),
                      tags$ol(
                        tags$li("Project Name: Enter the name of your project."),
                        tags$li("Add Variables: For each variable, fill in the details:",
                                tags$ul(
                                  tags$li("Enter the CT (Clinical Trial) variable name."),
                                  tags$li("Enter the Real World Data (RWD) variable name."),
                                  tags$li("Select the category (Inclusion Criteria, Exclusion Criteria, Outcome, Other)."),
                                  tags$li("Answer the yes/no questions about the variable's collection and availability."),
                                  tags$li("Provide detailed notes for each criterion where necessary."),
                                  tags$li("Use the 'General comments and observations' section for any additional remarks.")
                                )),
                        tags$li("Save or Reset: Click 'Add Variable' to save the variable or 'Reset Variable' to clear the form.")
                      ),
                      tags$h3("Step 2: Pocock's Criteria"),
                      tags$ol(
                        tags$li("Select Variable: Choose a variable from the dropdown menu."),
                        tags$li("Evaluate: For each criterion, indicate if it is met (Yes/No) and provide notes."),
                        tags$li("Save: Click 'Save Pocock Criteria' to save your evaluations.")
                      ),
                      tags$h3("Step 3: FDA Guidance"),
                      tags$ol(
                        tags$li("Select Variable: Choose a variable from the dropdown menu."),
                        tags$li("Evaluate: For each criterion, provide detailed notes on how you addressed it."),
                        tags$li("Save: Click 'Save FDA Criteria' to save your evaluations.")
                      ),
                      tags$h3("Downloading Reports"),
                      tags$ol(
                        tags$li("Navigate to the Download Report section."),
                        tags$li("Click 'Generate CSV Report' to download a comprehensive report of your assessments.")
                      ),
                      tags$h3("OMOP Data Scan"),
                      tags$p("This feature is coming soon and will allow for scanning OMOP-formatted data."),
                      tags$h3("Developed by"),
                      tags$p("For any questions or support, contact Danielle Boyce, MPH, DPA at danielle@boycedatascience.com.")
                    )
                )
              )
      )
    )
  )
)

server <- function(input, output, session) {
  variables <- reactiveVal(data.frame(
    Project_Name = character(),
    Variable_CT = character(),
    Variable_RWD = character(),
    Category = character(),
    Collected_Identically = character(),
    Collected_Identically_Notes = character(),
    Fully_Available = character(),
    Fully_Available_Notes = character(),
    Completely_Missing = character(),
    Completely_Missing_Notes = character(),
    Misclassification = character(),
    Misclassification_Notes = character(),
    Insufficient = character(),
    Insufficient_Notes = character(),
    Notes = character(),
    stringsAsFactors = FALSE
  ))
  
  pocock_criteria <- reactiveVal(data.frame(
    Variable = character(),
    Standard_Treatment_Consistency = character(),
    Standard_Treatment_Consistency_Notes = character(),
    Recent_Clinical_Study_Requirement = character(),
    Recent_Clinical_Study_Requirement_Notes = character(),
    Consistent_Evaluation_Methods = character(),
    Consistent_Evaluation_Methods_Notes = character(),
    Comparable_Patient_Characteristics = character(),
    Comparable_Patient_Characteristics_Notes = character(),
    Same_Organization_and_Investigators = character(),
    Same_Organization_and_Investigators_Notes = character(),
    No_Other_Differing_Indications = character(),
    No_Other_Differing_Indications_Notes = character(),
    stringsAsFactors = FALSE
  ))
  
  fda_guidance <- reactiveVal(data.frame(
    Variable = character(),
    FDA_Time_Periods = character(),
    FDA_Time_Periods_Notes = character(),
    FDA_Geographic_Region = character(),
    FDA_Geographic_Region_Notes = character(),
    FDA_Diagnosis_Criteria = character(),
    FDA_Diagnosis_Criteria_Notes = character(),
    FDA_Prognosis = character(),
    FDA_Prognosis_Notes = character(),
    FDA_Treatment_Attributes = character(),
    FDA_Treatment_Attributes_Notes = character(),
    FDA_Other_Treatment_Related_Factors = character(),
    FDA_Other_Treatment_Related_Factors_Notes = character(),
    FDA_Follow_Up_Periods = character(),
    FDA_Follow_Up_Periods_Notes = character(),
    FDA_Intercurrent_Events = character(),
    FDA_Intercurrent_Events_Notes = character(),
    FDA_Outcome_Measures = character(),
    FDA_Outcome_Measures_Notes = character(),
    FDA_Missing_Data = character(),
    FDA_Missing_Data_Notes = character(),
    stringsAsFactors = FALSE
  ))
  
  observeEvent(input$add_variable, {
    req(input$variable_name != "" || input$rwd_variable_name != "")
    
    new_variable <- data.frame(
      Project_Name = input$project_name,
      Variable_CT = input$variable_name,
      Variable_RWD = input$rwd_variable_name,
      Category = input$variable_category,
      Collected_Identically = ifelse(is.null(input$variable_collected_identically), "", input$variable_collected_identically),
      Collected_Identically_Notes = input$collected_identically_notes,
      Fully_Available = ifelse(is.null(input$variable_fully_available), "", input$variable_fully_available),
      Fully_Available_Notes = input$fully_available_notes,
      Completely_Missing = ifelse(is.null(input$variable_completely_missing), "", input$variable_completely_missing),
      Completely_Missing_Notes = input$completely_missing_notes,
      Misclassification = ifelse(is.null(input$variable_misclassification), "", input$variable_misclassification),
      Misclassification_Notes = input$misclassification_notes,
      Insufficient = ifelse(is.null(input$variable_insufficient), "", input$variable_insufficient),
      Insufficient_Notes = input$insufficient_notes,
      Notes = input$variable_notes,
      stringsAsFactors = FALSE
    )
    variables(rbind(variables(), new_variable))
    
    new_pocock_criteria <- data.frame(
      Variable = input$variable_name,
      Standard_Treatment_Consistency = "",
      Standard_Treatment_Consistency_Notes = "",
      Recent_Clinical_Study_Requirement = "",
      Recent_Clinical_Study_Requirement_Notes = "",
      Consistent_Evaluation_Methods = "",
      Consistent_Evaluation_Methods_Notes = "",
      Comparable_Patient_Characteristics = "",
      Comparable_Patient_Characteristics_Notes = "",
      Same_Organization_and_Investigators = "",
      Same_Organization_and_Investigators_Notes = "",
      No_Other_Differing_Indications = "",
      No_Other_Differing_Indications_Notes = "",
      stringsAsFactors = FALSE
    )
    pocock_criteria(rbind(pocock_criteria(), new_pocock_criteria))
    
    new_fda_guidance <- data.frame(
      Variable = input$variable_name,
      FDA_Time_Periods = "",
      FDA_Time_Periods_Notes = "",
      FDA_Geographic_Region = "",
      FDA_Geographic_Region_Notes = "",
      FDA_Diagnosis_Criteria = "",
      FDA_Diagnosis_Criteria_Notes = "",
      FDA_Prognosis = "",
      FDA_Prognosis_Notes = "",
      FDA_Treatment_Attributes = "",
      FDA_Treatment_Attributes_Notes = "",
      FDA_Other_Treatment_Related_Factors = "",
      FDA_Other_Treatment_Related_Factors_Notes = "",
      FDA_Follow_Up_Periods = "",
      FDA_Follow_Up_Periods_Notes = "",
      FDA_Intercurrent_Events = "",
      FDA_Intercurrent_Events_Notes = "",
      FDA_Outcome_Measures = "",
      FDA_Outcome_Measures_Notes = "",
      FDA_Missing_Data = "",
      FDA_Missing_Data_Notes = "",
      stringsAsFactors = FALSE
    )
    fda_guidance(rbind(fda_guidance(), new_fda_guidance))
    
    updateTextInput(session, "variable_name", value = "")
    updateTextInput(session, "rwd_variable_name", value = "")
    updateSelectInput(session, "variable_category", selected = character(0))
    updateRadioButtons(session, "variable_collected_identically", selected = character(0))
    updateRadioButtons(session, "variable_fully_available", selected = character(0))
    updateRadioButtons(session, "variable_completely_missing", selected = character(0))
    updateRadioButtons(session, "variable_misclassification", selected = character(0))
    updateRadioButtons(session, "variable_insufficient", selected = character(0))
    updateTextAreaInput(session, "collected_identically_notes", value = "")
    updateTextAreaInput(session, "fully_available_notes", value = "")
    updateTextAreaInput(session, "completely_missing_notes", value = "")
    updateTextAreaInput(session, "misclassification_notes", value = "")
    updateTextAreaInput(session, "insufficient_notes", value = "")
    updateTextAreaInput(session, "variable_notes", value = "")
    
    updateSelectInput(session, "selected_variable_pocock", choices = variables()$Variable_CT)
    updateSelectInput(session, "selected_variable_fda", choices = variables()$Variable_CT)
  })
  
  observeEvent(input$reset_variable, {
    updateTextInput(session, "project_name", value = "")
    updateTextInput(session, "variable_name", value = "")
    updateTextInput(session, "rwd_variable_name", value = "")
    updateSelectInput(session, "variable_category", selected = character(0))
    updateRadioButtons(session, "variable_collected_identically", selected = character(0))
    updateRadioButtons(session, "variable_fully_available", selected = character(0))
    updateRadioButtons(session, "variable_completely_missing", selected = character(0))
    updateRadioButtons(session, "variable_misclassification", selected = character(0))
    updateRadioButtons(session, "variable_insufficient", selected = character(0))
    updateTextAreaInput(session, "collected_identically_notes", value = "")
    updateTextAreaInput(session, "fully_available_notes", value = "")
    updateTextAreaInput(session, "completely_missing_notes", value = "")
    updateTextAreaInput(session, "misclassification_notes", value = "")
    updateTextAreaInput(session, "insufficient_notes", value = "")
    updateTextAreaInput(session, "variable_notes", value = "")
  })
  
  output$variables_table <- renderDataTable({
    datatable(variables(), editable = TRUE, options = list(paging = FALSE))
  })
  
  observeEvent(input$variables_table_cell_edit, {
    info <- input$variables_table_cell_edit
    updated_variables <- variables()
    updated_variables[info$row, info$col] <- info$value
    variables(updated_variables)
  })
  
  output$pocock_criteria_ui <- renderUI({
    req(input$selected_variable_pocock)
    selected_variable <- input$selected_variable_pocock
    i <- which(pocock_criteria()$Variable == selected_variable)
    req(i)
    criteria_labels <- c(
      "Standard Treatment Consistency",
      "Recent Clinical Study Requirement",
      "Consistent Evaluation Methods",
      "Comparable Patient Characteristics",
      "Same Organization and Investigators",
      "No Other Differing Indications"
    )
    criteria_descriptions <- c(
      "The historical control group must have received a precisely defined standard treatment, identical to the treatment for the randomized controls.",
      "The control group must have been part of a recent clinical study with the same patient eligibility requirements.",
      "Methods of treatment evaluation must be the same across both the historical and randomized control groups.",
      "Important patient characteristics should be comparable between the historical control group and the new trial participants.",
      "The previous study must have been conducted within the same organization and involved largely the same clinical investigators.",
      "There should be no other indications that might lead to different results between the randomized and historical controls."
    )
    tagList(
      h4(paste("Variable:", selected_variable)),
      lapply(1:6, function(j) {
        fluidRow(
          column(6, strong(criteria_labels[j]), tags$span(style = "font-size: smaller; font-style: italic;", criteria_descriptions[j])),
          column(3, radioButtons(paste0("pocock_yes_no_", selected_variable, "_", j), "", choices = c("Yes", "No"), inline = TRUE)),
          column(3, textAreaInput(paste0("pocock_notes_", selected_variable, "_", j), "Notes", "", rows = 5))
        )
      })
    )
  })
  
  output$fda_guidance_ui <- renderUI({
    req(input$selected_variable_fda)
    selected_variable <- input$selected_variable_fda
    i <- which(fda_guidance()$Variable == selected_variable)
    req(i)
    criteria_labels <- c(
      "Time Periods",
      "Geographic Region",
      "Diagnosis Criteria",
      "Prognosis",
      "Treatment Attributes",
      "Other Treatment Related Factors",
      "Follow Up Periods",
      "Intercurrent Events",
      "Outcome Measures",
      "Missing Data"
    )
    criteria_descriptions <- c(
      "Address differences in clinical care over time, ensuring comparable timeframes between treatment arms and external control arms to aid in interpreting study findings.",
      "Consider variations in standards of care and health care access across regions. Strive for a balance in participant distribution to reduce confounding effects.",
      "Ensure diagnostic criteria are consistent across compared arms to reduce bias from changes in diagnostic practices.",
      "Evaluate and ensure sufficient similarity in prognostic factors between participants in the treatment and external control arms.",
      "Ensure attributes of the treatment (formulation, dose, route, timing, frequency, and adherence) are comparable between treatment arms and the external control arm.",
      "Consider previous treatments, concomitant medications, and predictive biomarkers that could influence outcomes.",
      "Ensure the designation of index dates and follow-up durations are consistent across treatment and external control arms.",
      "Assess and account for the impact of intercurrent events (e.g., additional therapies) on treatment outcomes.",
      "Ensure endpoints are consistently measured and comparable across treatment arms and the external control arm.",
      "Assess and manage the extent of missing data to minimize bias and ensure data comparability."
    )
    tagList(
      h4(paste("Variable:", selected_variable)),
      lapply(1:10, function(j) {
        fluidRow(
          column(6, strong(criteria_labels[j]), tags$span(style = "font-size: smaller; font-style: italic;", criteria_descriptions[j])),
          column(6, textAreaInput(paste0("fda_notes_", selected_variable, "_", j), "Notes on this criterion", "", rows = 5))
        )
      })
    )
  })
  
  observeEvent(input$save_pocock, {
    selected_variable <- input$selected_variable_pocock
    i <- which(pocock_criteria()$Variable == selected_variable)
    req(i)
    current_pocock <- pocock_criteria()
    for (j in 1:6) {
      current_pocock[i, (2 * j) - 1] <- input[[paste0("pocock_yes_no_", selected_variable, "_", j)]]
      current_pocock[i, 2 * j] <- input[[paste0("pocock_notes_", selected_variable, "_", j)]]
    }
    pocock_criteria(current_pocock)
  })
  
  observeEvent(input$save_fda, {
    selected_variable <- input$selected_variable_fda
    i <- which(fda_guidance()$Variable == selected_variable)
    req(i)
    current_fda <- fda_guidance()
    for (j in 1:10) {
      current_fda[i, (2 * j)] <- input[[paste0("fda_notes_", selected_variable, "_", j)]]
    }
    fda_guidance(current_fda)
  })
  
  output$downloadCSVReport <- downloadHandler(
    filename = function() {
      paste("project_data-", Sys.Date(), ".csv", sep="")
    },
    content = function(file) {
      variables_df <- variables()
      pocock_df <- pocock_criteria()
      fda_df <- fda_guidance()
      
      # Align column names for Pocock
      names(pocock_df) <- c("Variable_CT", 
                            "Standard_Treatment_Consistency", "Standard_Treatment_Consistency_Notes",
                            "Recent_Clinical_Study_Requirement", "Recent_Clinical_Study_Requirement_Notes", 
                            "Consistent_Evaluation_Methods", "Consistent_Evaluation_Methods_Notes",
                            "Comparable_Patient_Characteristics", "Comparable_Patient_Characteristics_Notes",
                            "Same_Organization_and_Investigators", "Same_Organization_and_Investigators_Notes",
                            "No_Other_Differing_Indications", "No_Other_Differing_Indications_Notes")
      
      # Align column names for FDA
      names(fda_df) <- c("Variable_CT", 
                         "FDA_Time_Periods", "FDA_Time_Periods_Notes",
                         "FDA_Geographic_Region", "FDA_Geographic_Region_Notes", 
                         "FDA_Diagnosis_Criteria", "FDA_Diagnosis_Criteria_Notes", 
                         "FDA_Prognosis", "FDA_Prognosis_Notes", 
                         "FDA_Treatment_Attributes", "FDA_Treatment_Attributes_Notes", 
                         "FDA_Other_Treatment_Related_Factors", "FDA_Other_Treatment_Related_Factors_Notes", 
                         "FDA_Follow_Up_Periods", "FDA_Follow_Up_Periods_Notes", 
                         "FDA_Intercurrent_Events", "FDA_Intercurrent_Events_Notes", 
                         "FDA_Outcome_Measures", "FDA_Outcome_Measures_Notes", 
                         "FDA_Missing_Data", "FDA_Missing_Data_Notes")
      
      # Merge data frames and interleave notes with corresponding fields
      all_data <- variables_df %>%
        left_join(pocock_df, by = "Variable_CT") %>%
        left_join(fda_df, by = "Variable_CT")
      
      write.csv(all_data, file, row.names = FALSE)
    },
    contentType = "text/csv"
  )
}

shinyApp(ui = ui, server = server)




